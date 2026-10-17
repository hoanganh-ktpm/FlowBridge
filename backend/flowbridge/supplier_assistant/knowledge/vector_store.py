# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Chroma stores derived vectors + reference metadata; source text stays in its store."""

from pathlib import Path
from typing import Protocol

from .models import ChunkRecord, KnowledgeError


class VectorStore(Protocol):
    def put(
        self, collection: str, chunks: list[ChunkRecord], vectors: list[list[float]]
    ) -> None: ...

    def ids(self, collection: str) -> set[str]: ...

    def search(
        self, collection: str, vector: list[float], documents: set[str], limit: int
    ) -> list[str]: ...


class ChromaVectors:
    def __init__(self, root: Path):
        self.root = root
        self._client = None

    def _db(self):
        if self._client is None:
            try:
                import chromadb
                from chromadb.config import Settings

                self._client = chromadb.PersistentClient(
                    path=str(self.root), settings=Settings(anonymized_telemetry=False)
                )
            except Exception as exc:
                raise KnowledgeError("VECTOR_STORE_UNAVAILABLE: cannot open local Chroma") from exc
        return self._client

    def put(self, collection: str, chunks: list[ChunkRecord], vectors: list[list[float]]) -> None:
        try:
            target = self._db().get_or_create_collection(
                collection, embedding_function=None, configuration={"hnsw": {"space": "cosine"}}
            )
            for start in range(0, len(chunks), 64):
                batch = chunks[start : start + 64]
                target.upsert(
                    ids=[chunk.reference.chunk_id for chunk in batch],
                    embeddings=vectors[start : start + 64],
                    metadatas=[
                        {
                            "document_id": chunk.reference.document_id,
                            "supplier_id": str(chunk.reference.supplier_id),
                            "checksum": chunk.reference.checksum,
                        }
                        for chunk in batch
                    ],
                )
        except Exception as exc:
            raise KnowledgeError("VECTOR_STORE_WRITE_FAILED: generation was not activated") from exc

    def ids(self, collection: str) -> set[str]:
        try:
            return set(self._db().get_collection(collection, embedding_function=None).get()["ids"])
        except Exception as exc:
            raise KnowledgeError("VECTOR_STORE_READ_FAILED: index is absent or damaged") from exc

    def search(
        self, collection: str, vector: list[float], documents: set[str], limit: int
    ) -> list[str]:
        if not documents:
            return []
        try:
            result = (
                self._db()
                .get_collection(collection, embedding_function=None)
                .query(
                    query_embeddings=[vector],
                    where={"document_id": {"$in": sorted(documents)}},
                    n_results=limit,
                    include=["distances"],
                )
            )
            return result["ids"][0]
        except Exception as exc:
            raise KnowledgeError("VECTOR_SEARCH_FAILED: cannot query local Chroma") from exc
