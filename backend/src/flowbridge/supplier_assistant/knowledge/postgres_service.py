# SPDX-License-Identifier: GPL-3.0-or-later
"""PostgreSQL exact cosine + existing BM25/RRF with final live ACL revalidation."""

import asyncio
import hashlib
import threading
from collections.abc import Awaitable, Callable

from starlette.concurrency import run_in_threadpool

from flowbridge.shared.errors import CoreError, ErrorCode

from .models import KnowledgeError, RetrievalRequest, RetrievalResponse
from .postgres_domain import (
    PgScope,
    PgVersion,
    prepare_chunks,
    validate_stored_chunks,
)
from .postgres_store import PgKnowledgeStore
from .retrieval import fuse_ranks, lexical_rank

ScopeResolver = Callable[[], Awaitable[PgScope]]


class PgKnowledgeService:
    def __init__(self, store: PgKnowledgeStore, embedder):
        self.store, self.embedder = store, embedder
        self._gate = asyncio.Lock()
        self._model_lock = threading.Lock()

    async def cpu(self, function, *args):
        def work():
            # The worker owns this mutex even when the awaiting HTTP task is cancelled.
            with self._model_lock:
                return function(*args)

        return await run_in_threadpool(work)

    def close(self):
        with self._model_lock:
            if hasattr(self.embedder, "_model"):
                self.embedder._model = None

    async def search(self, request: RetrievalRequest, resolve_scope: ScopeResolver) -> dict:
        if self._gate.locked():
            raise CoreError(ErrorCode.RATE_LIMITED, retry_after=1)
        async with self._gate:
            scope = await resolve_scope()
            versions = await self.store.versions(scope)
            prepared = [(v, prepare_chunks(v, self.embedder.profile)) for v in versions]
            stored = await self.store.chunks(
                [chunk for _, chunks in prepared for chunk in chunks],
                self.embedder.profile,
            )
            ready = []
            warnings = [
                "RRF_SCORE_IS_RANK_UTILITY: relevance_score is not a probability",
                "RERANK_DISABLED",
            ]
            for version, chunks in prepared:
                rows = [r for r in stored if r["version_id"] == version.version_id]
                if validate_stored_chunks(chunks, rows):
                    ready.extend(chunks)
                else:
                    warnings.append(f"INDEX_NOT_READY: {version.document_id}")
            by_id = {c.record.reference.chunk_id: c for c in ready}
            if ready:
                vector = await self.cpu(self.embedder.query, request.query)
                dense = await self.store.dense(ready, self.embedder.profile, vector)
                sparse = await self.cpu(
                    lexical_rank,
                    request.query,
                    [c.record for c in ready],
                    40,
                )
                fused = fuse_ranks([key for key in dense if key in by_id], sparse)
            else:
                fused = []
                warnings.append("NO_ELIGIBLE_INDEXED_SOURCE")
            # Re-authorize identities, job/resource lineage, ACL and published source versions
            # after CPU work. Never serve a cached quote after a role/source is revoked.
            current_scope = await resolve_scope()
            if current_scope != scope:
                raise CoreError(ErrorCode.REQUEST_CHANGED)
            current = {v.version_id: v for v in await self.store.versions(current_scope)}
            original = {v.version_id: v for v in versions}
            output = []
            for key, utility in fused:
                chunk = by_id[key]
                if current.get(chunk.version_id) != original.get(chunk.version_id):
                    warnings.append("SOURCE_CHANGED_DURING_QUERY")
                    continue
                # prepare_chunks verified the complete source hash and exact UTF-8 slice.
                output.append(
                    {
                        "reference": chunk.record.reference.model_dump(mode="json"),
                        "text": chunk.content,
                        "relevance_score": min(1.0, utility / (2.0 / 61.0)),
                    }
                )
                if len(output) == request.top_k:
                    break
            version_material = "|".join(sorted(c.generation for c in ready))
            index_version = "pgvector:" + hashlib.sha256(version_material.encode()).hexdigest()
            return RetrievalResponse(
                query=request.query,
                chunks=output,
                index_version=index_version,
                warnings=list(dict.fromkeys(warnings)),
            ).model_dump(mode="json")

    async def index_versions(
        self,
        versions: list[PgVersion],
        resolve_scope: ScopeResolver,
        *,
        references: list[dict] | None = None,
    ) -> dict:
        if self._gate.locked():
            raise CoreError(ErrorCode.RATE_LIMITED, retry_after=1)
        async with self._gate:
            scope = await resolve_scope()
            chunks_by_version = [(v, prepare_chunks(v, self.embedder.profile)) for v in versions]
            all_chunks = [c for _, chunks in chunks_by_version for c in chunks]
            if references is not None:
                available = {
                    c.record.reference.chunk_id: c.record.reference.model_dump(mode="json")
                    for c in all_chunks
                }
                if any(available.get(ref["chunk_id"]) != ref for ref in references):
                    raise CoreError(ErrorCode.VALIDATION_ERROR)
            stored = await self.store.chunks(all_chunks, self.embedder.profile)
            for version, chunks in chunks_by_version:
                rows = [r for r in stored if r["version_id"] == version.version_id]
                if validate_stored_chunks(chunks, rows):
                    continue
                vectors = await self.cpu(
                    self.embedder.documents,
                    [chunk.embedding_input for chunk in chunks],
                )
                if await resolve_scope() != scope:
                    raise CoreError(ErrorCode.REQUEST_CHANGED)
                await self.store.write_generation(
                    version, chunks, vectors, self.embedder.profile, scope
                )
            # A caller can lose authorization during a final transaction, too.
            if await resolve_scope() != scope:
                raise CoreError(ErrorCode.REQUEST_CHANGED)
            current = {v.version_id: v for v in await self.store.versions(scope, latest=False)}
            if any(current.get(v.version_id) != v for v in versions):
                raise CoreError(ErrorCode.REQUEST_CHANGED)
            material = "|".join(sorted(c.generation for c in all_chunks))
            return {
                "index_version": "pgvector:" + hashlib.sha256(material.encode()).hexdigest(),
                "indexed_documents": len({v.document_id for v in versions}),
                "indexed_chunks": len(all_chunks),
                "warnings": [
                    "POSTGRES_PGVECTOR: exact cosine + BM25; no Chroma dependency at runtime"
                ],
            }

    async def index_references(self, references: list[dict], resolve_scope: ScopeResolver) -> dict:
        scope = await resolve_scope()
        versions = await self.store.versions(scope, latest=False)
        requested = {ref["document_version"] for ref in references}
        eligible = [
            v
            for v in versions
            if str(v.version_id) in requested
            and v.status == "published"
            and v.known_from <= scope.as_of
            and v.published_at <= scope.as_of
        ]
        if {str(v.version_id) for v in eligible} != requested:
            raise CoreError(ErrorCode.NOT_FOUND)
        return await self.index_versions(eligible, resolve_scope, references=references)

    async def resolve(self, reference: dict, resolve_scope: ScopeResolver) -> dict:
        scope = await resolve_scope()
        versions = await self.store.versions(scope, latest=False)
        version = next(
            (
                v
                for v in versions
                if str(v.version_id) == reference["document_version"]
                and v.status == "published"
                and v.known_from <= scope.as_of
                and v.published_at <= scope.as_of
            ),
            None,
        )
        if version is None:
            raise KnowledgeError("CHUNK_UNAVAILABLE: source is withdrawn or outside scope")
        # Retained old profile/generation rows resolve without re-embedding or using
        # the current chunker. The stored reference must still match canonical bytes.
        result = await self.store.resolve_reference(version, reference)
        if await resolve_scope() != scope:
            raise CoreError(ErrorCode.REQUEST_CHANGED)
        current = {v.version_id: v for v in await self.store.versions(scope, latest=False)}
        if current.get(version.version_id) != version:
            raise KnowledgeError("CHUNK_UNAVAILABLE: source changed during resolution")
        return result
