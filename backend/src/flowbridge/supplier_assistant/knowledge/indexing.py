# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Immutable generations; activate only a complete, verified local index."""

import json
import os
from pathlib import Path

from .chunking import CHUNKER_VERSION, TOKENIZER_VERSION, chunk_document, embedding_text
from .embeddings import Embedder
from .models import IndexSnapshot, KnowledgeError
from .sources import LocalSources, sha256
from .vector_store import VectorStore


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def load_snapshot(root: Path) -> IndexSnapshot:
    try:
        pointer = json.loads((root / "active.json").read_text(encoding="utf-8"))
        version = pointer["index_version"]
        if len(version) != 64 or any(char not in "0123456789abcdef" for char in version):
            raise ValueError("Invalid index version")
        snapshot = IndexSnapshot.model_validate_json(
            (root / f"{version}.json").read_text(encoding="utf-8")
        )
        if snapshot.index_version != version or snapshot.schema_version != "rag-index-v1":
            raise ValueError("Invalid snapshot")
        return snapshot
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise KnowledgeError("INDEX_UNAVAILABLE: run the index command first") from exc


def build_index(
    sources: LocalSources, root: Path, embedder: Embedder, vectors: VectorStore
) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".write.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise KnowledgeError("INDEX_BUSY: another writer or a stale .write.lock exists") from exc
    try:
        os.close(descriptor)
        catalog = sources.catalog()
        documents = sorted(catalog.documents, key=lambda spec: str(spec.document_id))
        chunks = []
        texts = []
        for spec in documents:
            text = sources.read(spec)
            records = chunk_document(catalog.dataset_id, spec, text)
            chunks.extend(records)
            texts.extend(
                embedding_text(spec, chunk, text[chunk.start_char : chunk.end_char])
                for chunk in records
            )
        if not chunks:
            raise KnowledgeError("EMPTY_CORPUS: no nonempty Markdown sections")
        fingerprint = {
            "dataset_id": catalog.dataset_id,
            "documents": [doc.model_dump(mode="json") for doc in documents],
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "chunker": CHUNKER_VERSION,
            "tokenizer": TOKENIZER_VERSION,
            "embedding": embedder.profile,
        }
        version = sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True))
        snapshot = IndexSnapshot(
            index_version=version,
            collection=f"rag_{version[:48]}",
            embedding_profile=embedder.profile,
            documents=documents,
            chunks=chunks,
        )
        expected_ids = {chunk.reference.chunk_id for chunk in chunks}
        if len(expected_ids) != len(chunks):
            raise KnowledgeError("DUPLICATE_CHUNK_ID: index was not activated")
        snapshot_path = root / f"{version}.json"
        reuse = False
        if snapshot_path.exists():
            # Metadata changes create a generation; repeat exact builds do no ML work.
            try:
                reuse = vectors.ids(snapshot.collection) == expected_ids
            except KnowledgeError:
                reuse = False
        if not reuse:
            embeddings = embedder.documents(texts)
            if len(embeddings) != len(chunks):
                raise KnowledgeError("EMBEDDING_COUNT_MISMATCH: index was not activated")
            vectors.put(snapshot.collection, chunks, embeddings)
            if vectors.ids(snapshot.collection) != expected_ids:
                raise KnowledgeError("INCOMPLETE_INDEX: index was not activated")
        # Do not publish a snapshot if source bindings/content changed during a slow embedding pass.
        if sources.catalog() != catalog:
            raise KnowledgeError("CATALOG_CHANGED: retry indexing")
        for spec in documents:
            sources.read(spec)
        _atomic_json(snapshot_path, snapshot.model_dump(mode="json"))
        _atomic_json(root / "active.json", {"index_version": version})
        return {
            "index_version": version,
            "indexed_documents": len(documents),
            "indexed_chunks": len(chunks),
            "warnings": ["LOCAL_FIXTURE_SOURCE: Core document storage and ACL are not connected"],
        }
    finally:
        lock.unlink(missing_ok=True)
