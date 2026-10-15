# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
# HTTP/lifecycle evidence with controlled retrieval; real Qwen is tested separately.

import asyncio
import os
from pathlib import Path
from threading import Event
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.supplier_assistant.contract import validate_dto
from flowbridge.supplier_assistant.knowledge import development
from flowbridge.supplier_assistant.knowledge.chunking import chunk_document
from flowbridge.supplier_assistant.knowledge.models import (
    KnowledgeChunk,
    KnowledgeError,
    RetrievalResponse,
)
from flowbridge.supplier_assistant.knowledge.router import PATH
from flowbridge.supplier_assistant.knowledge.sources import LocalSources

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "examples/rag/catalog.json"
HEADERS = {"Idempotency-Key": "rag-http-test", "X-Correlation-Id": "RAG-REQ-1"}


class ControlledRetriever:
    def __init__(self):
        self.sources = LocalSources(CATALOG)
        self.calls = []
        self.error = None
        self.started = Event()
        self.release = Event()
        self.block = False

    def search(self, query, scope, *, top_k):
        self.calls.append((query, scope, top_k))
        if self.block:
            self.started.set()
            if not self.release.wait(5):
                raise RuntimeError("Test worker was not released")
        if self.error is not None:
            raise self.error
        catalog = self.sources.catalog()
        docs = [
            doc
            for doc in catalog.documents
            if doc.document_id in scope.allowed_document_ids
            and (scope.supplier_id is None or doc.supplier_id == scope.supplier_id)
        ]
        chunks = []
        if docs:
            spec = docs[0]
            text = self.sources.read(spec)
            chunk = chunk_document(catalog.dataset_id, spec, text)[0]
            chunks = [
                KnowledgeChunk(
                    reference=chunk.reference,
                    text=text[chunk.start_char : chunk.end_char],
                    relevance_score=0.5,
                )
            ]
        return RetrievalResponse(
            query=query,
            chunks=chunks,
            index_version="controlled-test-v1",
            warnings=["CONTROLLED_TEST_RETRIEVAL"],
        )


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for key in os.environ:
        if key.startswith("FLOWBRIDGE_"):
            monkeypatch.delenv(key)


@pytest.fixture
def controlled(monkeypatch):
    instances = []

    def factory(settings):
        instance = ControlledRetriever()
        instances.append(instance)
        return instance

    monkeypatch.setattr(development, "create_retriever", factory)
    return instances


def configuration(**overrides):
    return Settings(
        _env_file=None,
        environment="test",
        rag_dev_enabled=True,
        rag_dev_catalog_path=CATALOG,
        **overrides,
    )


def transport(app, *, remote=False):
    return httpx.ASGITransport(app=app, client=("198.51.100.10" if remote else "127.0.0.1", 12345))


@pytest.mark.asyncio
async def test_default_flag_off_has_no_route_or_retriever(controlled):
    app = create_app(Settings(_env_file=None))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url="http://test") as client:
            assert (await client.post(PATH, json={"query": "giao trễ"})).status_code == 404
        assert not controlled and app.state.rag_dev_service is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "production"},
        {"host": "0.0.0.0"},
        {"host": "192.168.1.10"},
        {"database_url": "postgresql+asyncpg://user:secret@localhost/database"},
        {"database_host": "localhost", "database_user": "user", "database_password": "secret"},
    ],
)
def test_dev_configuration_rejects_production_network_bind_and_dsn(overrides):
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, rag_dev_enabled=True, **overrides)
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_reuses_app_retriever_matches_contract_and_cleans_up(controlled):
    app = create_app(configuration())
    supplier = LocalSources(CATALOG).catalog().documents[0].supplier_id
    assert not controlled
    async with app.router.lifespan_context(app):
        service = app.state.rag_dev_service
        async with httpx.AsyncClient(transport=transport(app), base_url="http://test") as client:
            for _ in range(2):
                response = await client.post(
                    PATH,
                    json={"query": "giao trễ", "supplier_id": str(supplier), "top_k": 3},
                    headers=HEADERS,
                )
                assert response.status_code == 200
                validate_dto(response.json(), "RetrievalResponse")
                assert response.headers["x-flowbridge-rag-mode"] == "development-local-fixtures"
                assert response.headers["x-correlation-id"] == "RAG-REQ-1"
                assert any("DEVELOPMENT_ONLY" in w for w in response.json()["warnings"])
        assert len(controlled) == 1 and len(controlled[0].calls) == 2
        _, scope, top_k = controlled[0].calls[-1]
        assert top_k == 3 and scope.supplier_id == supplier
        assert scope.allowed_document_ids == frozenset(
            doc.document_id for doc in LocalSources(CATALOG).catalog().documents
        )
    assert app.state.rag_dev_service is None and service._retriever is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": ""},
        {"query": "  \n"},
        {"query": "x" * 2001},
        {"query": 123},
        {"query": "q", "top_k": 0},
        {"query": "q", "top_k": 21},
        {"query": "q", "top_k": True},
        {"query": "q", "top_k": "3"},
        {"query": "q", "supplier_id": "bad-private-value"},
        {"query": "q", "supplier_id": None},
        {"query": "q", "top_k": None},
        {"query": "q", "allowed_document_ids": ["bad-private-value"]},
        {"query": "q", "as_of": "2026-10-15T00:00:00Z"},
        {"query": "q", "role": "ADMIN"},
    ],
)
@pytest.mark.asyncio
async def test_invalid_body_and_client_permission_fields_rejected(controlled, payload):
    app = create_app(configuration())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url="http://test") as client:
            response = await client.post(PATH, json=payload, headers=HEADERS)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert "bad-private-value" not in response.text
    assert not controlled[0].calls


@pytest.mark.asyncio
async def test_idempotency_header_is_required_and_malformed_json_is_sanitized(controlled):
    app = create_app(configuration())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url="http://test") as client:
            for headers in ({}, {"Idempotency-Key": "x" * 129}):
                assert (
                    await client.post(PATH, json={"query": "q"}, headers=headers)
                ).status_code == 422
            response = await client.post(
                PATH,
                content=b'{"query":"private-input",',
                headers={**HEADERS, "Content-Type": "application/json"},
            )
    assert response.status_code == 400
    assert "private-input" not in response.text and not controlled[0].calls


@pytest.mark.asyncio
async def test_remote_peer_is_rejected_even_with_forwarded_local_headers(controlled):
    app = create_app(configuration())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport(app, remote=True), base_url="http://test"
        ) as client:
            response = await client.post(
                PATH, json={"query": "q"}, headers={**HEADERS, "X-Forwarded-For": "127.0.0.1"}
            )
    assert response.status_code == 403 and not controlled[0].calls


@pytest.mark.asyncio
async def test_unknown_supplier_returns_empty_instead_of_other_suppliers(controlled):
    app = create_app(configuration())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url="http://test") as client:
            response = await client.post(
                PATH, json={"query": "q", "supplier_id": str(uuid4())}, headers=HEADERS
            )
    assert response.status_code == 200 and response.json()["chunks"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (KnowledgeError("MODEL_UNAVAILABLE: sensitive-local-path"), 503, "MODEL_UNAVAILABLE"),
        (KnowledgeError("INDEX_UNAVAILABLE: sensitive-local-path"), 503, "UPSTREAM_UNAVAILABLE"),
        (RuntimeError("sensitive-local-path"), 500, "INTERNAL_ERROR"),
    ],
)
async def test_runtime_errors_use_existing_sanitized_envelope(controlled, error, status, code):
    app = create_app(configuration())
    async with app.router.lifespan_context(app):
        controlled[0].error = error
        async with httpx.AsyncClient(transport=transport(app), base_url="http://test") as client:
            response = await client.post(PATH, json={"query": "q"}, headers=HEADERS)
    assert response.status_code == status and response.json()["code"] == code
    assert response.json()["request_id"] == "RAG-REQ-1"
    assert "sensitive-local-path" not in response.text


@pytest.mark.asyncio
async def test_busy_worker_returns_429_without_blocking_event_loop(controlled):
    app = create_app(configuration())
    async with app.router.lifespan_context(app):
        fake = controlled[0]
        fake.block = True
        async with httpx.AsyncClient(transport=transport(app), base_url="http://test") as client:
            first = asyncio.create_task(client.post(PATH, json={"query": "q"}, headers=HEADERS))
            try:
                assert await asyncio.to_thread(fake.started.wait, 2)
                second = await client.post(PATH, json={"query": "q"}, headers=HEADERS)
                assert second.status_code == 429 and second.headers["retry-after"] == "1"
                assert (await client.get("/_ops/live")).status_code == 200
            finally:
                fake.release.set()
            assert (await first).status_code == 200


def test_swagger_documents_real_route_without_restoring_retired_sample_api(controlled):
    app = create_app(configuration(docs_enabled=True))
    schema = app.openapi()
    operation = schema["paths"][PATH]["post"]
    assert operation["x-development-only"] and operation["security"] == []
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in operation["parameters"])
    assert set(schema["paths"]) == {PATH}
    assert "x-sample-mode" not in schema
    assert schema["x-rag-development-mode"] and not controlled
    assert app.openapi() is schema
    assert not hasattr(app.state, "sample_context")
