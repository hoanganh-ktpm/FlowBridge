# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Filter first, combine dense and lexical ranks, then resolve verifiable quotations."""

from pathlib import Path
from typing import Protocol
from uuid import UUID

from .chunking import tokenize
from .embeddings import Embedder
from .indexing import load_snapshot
from .models import (
    ChunkRecord,
    DocumentSpec,
    KnowledgeChunk,
    KnowledgeError,
    RetrievalResponse,
    RetrievalScope,
)
from .sources import LocalSources, sha256
from .vector_store import VectorStore

RRF_K = 60
CANDIDATES_PER_CHANNEL = 40
RERANK_POOL = 20


class Reranker(Protocol):
    def order(self, query: str, passages: list[str]) -> list[int]: ...


def fuse_ranks(*rankings: list[str]) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        # A repeated vector ID must never earn additional votes.
        for position, chunk_id in enumerate(dict.fromkeys(ranking), start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + position)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def lexical_rank(query: str, chunks: list[ChunkRecord], limit: int) -> list[str]:
    if not chunks:
        return []
    try:
        from rank_bm25 import BM25Okapi
    except ImportError as exc:
        raise KnowledgeError("BM25_UNAVAILABLE: install the rag extra") from exc
    terms = tokenize(query)
    corpus = [chunk.tokens or ["__empty__"] for chunk in chunks]
    scores = BM25Okapi(corpus).get_scores(terms)
    # rank_bm25 may yield zero/negative IDF in tiny corpora. Keep matching entries,
    # never manufacture relevance for passages with no lexical term overlap.
    candidates = [
        (chunk.reference.chunk_id, float(score))
        for chunk, score in zip(chunks, scores, strict=True)
        if set(terms).intersection(chunk.tokens)
    ]
    return [key for key, _ in sorted(candidates, key=lambda item: (-item[1], item[0]))[:limit]]


def _visible(spec: DocumentSpec, scope: RetrievalScope) -> bool:
    return (
        spec.document_id in scope.allowed_document_ids
        and (scope.supplier_id is None or scope.supplier_id == spec.supplier_id)
        and (scope.as_of is None or spec.known_from <= scope.as_of)
    )


class Retriever:
    def __init__(
        self,
        sources: LocalSources,
        root: Path,
        embedder: Embedder,
        vectors: VectorStore,
        reranker: Reranker | None = None,
    ):
        self.sources = sources
        self.root = root
        self.embedder = embedder
        self.vectors = vectors
        self.reranker = reranker

    def _eligible(self, snapshot, scope: RetrievalScope):
        current = {spec.document_id: spec for spec in self.sources.catalog().documents}
        eligible = {}
        warnings = []
        for spec in snapshot.documents:
            if not _visible(spec, scope):
                continue
            # A removed/rebound source is excluded even before reindexing.
            if current.get(spec.document_id) != spec:
                warnings.append(f"SOURCE_BINDING_CHANGED: {spec.document_id}; re-index")
                continue
            try:
                self.sources.read(spec)
            except KnowledgeError:
                warnings.append(f"SOURCE_UNAVAILABLE_OR_CHANGED: {spec.document_id}; re-index")
                continue
            eligible[str(spec.document_id)] = spec
        return eligible, warnings

    def resolve(self, chunk_id: str, scope: RetrievalScope) -> dict:
        snapshot = load_snapshot(self.root)
        chunk = next((c for c in snapshot.chunks if c.reference.chunk_id == chunk_id), None)
        if chunk is None:
            raise KnowledgeError("CHUNK_UNAVAILABLE: reference is not in the active generation")
        eligible, _ = self._eligible(snapshot, scope)
        spec = eligible.get(chunk.reference.document_id)
        if spec is None:
            raise KnowledgeError("CHUNK_UNAVAILABLE: source is outside scope, changed or removed")
        text = self.sources.read(spec)
        quote = text[chunk.start_char : chunk.end_char]
        if sha256(quote) != chunk.text_sha256:
            raise KnowledgeError("SOURCE_CHANGED: quotation checksum does not match")
        return {
            "reference": chunk.reference.model_dump(mode="json"),
            "text": quote,
            "heading_path": chunk.heading_path,
            "reviewer_user_id": str(chunk.reviewer_user_id),
            "reviewer_name": chunk.reviewer_name,
            "source_path": spec.path,
            "start_char": chunk.start_char,
            "end_char": chunk.end_char,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "known_from": spec.known_from.isoformat(),
            "report_asof": spec.report_asof.isoformat(),
        }

    def search(self, query: str, scope: RetrievalScope, *, top_k: int = 5) -> RetrievalResponse:
        if not query.strip() or len(query) > 2000 or not 1 <= top_k <= 20:
            raise KnowledgeError("INVALID_QUERY: nonempty query <=2000 chars and top_k 1..20")
        snapshot = load_snapshot(self.root)
        if snapshot.embedding_profile != self.embedder.profile:
            raise KnowledgeError("EMBEDDING_PROFILE_MISMATCH: re-index with the same pinned model")
        eligible, warnings = self._eligible(snapshot, scope)
        warnings.append("RRF_SCORE_IS_RANK_UTILITY: relevance_score is not a probability")
        warnings.append("LOCAL_OPERATOR_SCOPE: no production authentication or ACL integration")
        chunks = [chunk for chunk in snapshot.chunks if chunk.reference.document_id in eligible]
        if not chunks:
            warnings.append("NO_ELIGIBLE_SOURCE: check allowlist, supplier and as-of time")
            return RetrievalResponse(
                query=query, chunks=[], index_version=snapshot.index_version, warnings=warnings
            )
        by_id = {chunk.reference.chunk_id: chunk for chunk in chunks}
        dense = self.vectors.search(
            snapshot.collection,
            self.embedder.query(query),
            set(eligible),
            min(CANDIDATES_PER_CHANNEL, len(chunks)),
        )
        dense = [chunk_id for chunk_id in dense if chunk_id in by_id]
        sparse = lexical_rank(query, chunks, CANDIDATES_PER_CHANNEL)
        fused = fuse_ranks(dense, sparse)[:RERANK_POOL]
        # Resolve against CURRENT sources before exposing passages to an optional reranker.
        resolved = {key: self.resolve(key, scope) for key, _ in fused}
        if self.reranker is not None and fused:
            try:
                order = self.reranker.order(query, [resolved[key]["text"] for key, _ in fused])
                if (
                    not isinstance(order, list)
                    or any(type(index) is not int for index in order)
                    or sorted(order) != list(range(len(fused)))
                ):
                    raise ValueError("Expected a unique, complete permutation")
                fused = [fused[index] for index in order]
            except Exception:
                warnings.append("RERANK_FAILED: using original RRF ordering")
        else:
            warnings.append("RERANK_DISABLED")
        results = []
        for key, utility in fused[:top_k]:
            # Recheck source bindings after a potentially slow external reranker adapter.
            quote = self.resolve(key, scope)
            results.append(
                KnowledgeChunk(
                    reference=by_id[key].reference,
                    text=quote["text"],
                    relevance_score=min(1.0, utility / (2.0 / (RRF_K + 1))),
                )
            )
        return RetrievalResponse(
            query=query, chunks=results, index_version=snapshot.index_version, warnings=warnings
        )


def local_operator_scope(
    sources: LocalSources,
    *,
    supplier_id: UUID | None = None,
    as_of=None,
    allowed: frozenset[UUID] | None = None,
) -> RetrievalScope:
    """Explicit CLI trust boundary; never use as a web request's permission resolver."""
    return RetrievalScope(
        allowed_document_ids=(
            frozenset(doc.document_id for doc in sources.catalog().documents)
            if allowed is None
            else allowed
        ),
        supplier_id=supplier_id,
        as_of=as_of,
    )
