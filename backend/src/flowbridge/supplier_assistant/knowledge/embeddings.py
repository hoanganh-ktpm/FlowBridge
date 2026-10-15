# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Lazy, CPU-only local embedding; no document content is sent to a remote API."""

from pathlib import Path
from typing import Protocol

from .models import KnowledgeError

MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
QUERY_INSTRUCTION = (
    "Instruct: Retrieve supplier review passages that answer the purchasing question.\nQuery: "
)


class Embedder(Protocol):
    profile: str

    def documents(self, texts: list[str]) -> list[list[float]]: ...

    def query(self, text: str) -> list[float]: ...


class QwenEmbedder:
    profile = (
        f"{MODEL_ID}@{MODEL_REVISION}:1024:cosine:normalized:cpu-fp32:seq2048:"
        "query-instruction-v1:document-context-v1"
    )

    def __init__(self, cache: Path, *, allow_download: bool = False, batch_size: int = 4):
        self.cache = cache
        self.allow_download = allow_download
        self.batch_size = batch_size
        self._model = None

    def _encode(self, texts: list[str]) -> list[list[float]]:
        try:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(
                    MODEL_ID,
                    revision=MODEL_REVISION,
                    device="cpu",
                    cache_folder=str(self.cache),
                    local_files_only=not self.allow_download,
                    trust_remote_code=False,
                    model_kwargs={"dtype": "float32"},
                )
                self._model.max_seq_length = 2048
            vectors = self._model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            if vectors.shape != (len(texts), 1024):
                raise ValueError("Unexpected embedding shape")
            return vectors.tolist()
        except Exception as exc:
            raise KnowledgeError(
                "MODEL_UNAVAILABLE: install the rag extra and download the pinned model with "
                "index --allow-download; check local memory and model cache"
            ) from exc

    def documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def query(self, text: str) -> list[float]:
        return self._encode([QUERY_INSTRUCTION + text])[0]
