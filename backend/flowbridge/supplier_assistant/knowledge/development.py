# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""App-owned local fixture retrieval; not a production authorization adapter."""

from threading import Lock

from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode

from .embeddings import QwenEmbedder
from .models import KnowledgeError, RetrievalRequest, RetrievalResponse, RetrievalScope
from .retrieval import Retriever
from .sources import LocalSources
from .vector_store import ChromaVectors

DEV_NOTICE = (
    "DEVELOPMENT_ONLY: real retrieval over the configured local fixture catalog; "
    "production caller authentication and document ACL are not connected"
)


def create_retriever(settings: Settings) -> Retriever:
    # Constructors are lazy: neither download nor model loading happens at startup.
    return Retriever(
        LocalSources(settings.rag_dev_catalog_path),
        settings.rag_dev_index_path,
        QwenEmbedder(settings.rag_dev_model_cache_path, allow_download=False),
        ChromaVectors(settings.rag_dev_index_path / "chroma"),
    )


class DevelopmentKnowledgeService:
    def __init__(self, settings: Settings):
        self._retriever: Retriever | None = create_retriever(settings)
        self._busy = Lock()

    def search(self, body: RetrievalRequest) -> RetrievalResponse:
        # Held INSIDE the worker until completion, even if the HTTP caller disconnects.
        # This also prevents concurrent first-load and simultaneous CPU inference.
        if not self._busy.acquire(blocking=False):
            raise CoreError(ErrorCode.RATE_LIMITED, retry_after=1)
        try:
            retriever = self._retriever
            if retriever is None:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            # Explicit trusted local-operator scope. No client-provided roles/allowlist.
            catalog = retriever.sources.catalog()
            scope = RetrievalScope(
                allowed_document_ids=frozenset(doc.document_id for doc in catalog.documents),
                supplier_id=body.supplier_id,
            )
            result = retriever.search(body.query, scope, top_k=body.top_k)
            return result.model_copy(update={"warnings": [*result.warnings, DEV_NOTICE]})
        except KnowledgeError as exc:
            code = str(exc).split(":", 1)[0]
            if code == "MODEL_UNAVAILABLE":
                raise CoreError(ErrorCode.MODEL_UNAVAILABLE) from None
            if code == "INVALID_QUERY":
                raise CoreError(ErrorCode.VALIDATION_ERROR) from None
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        finally:
            self._busy.release()

    def close(self) -> None:
        # Shutdown waits for an active worker; dropping the retriever releases its model.
        with self._busy:
            self._retriever = None
