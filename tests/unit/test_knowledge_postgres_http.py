# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime contract/auth boundaries; database and IdP IO are controlled here."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from flowbridge.db import get_session
from flowbridge.identity.authentication import VerifiedIdentity, authenticated_identity
from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.contract import validate_dto
from flowbridge.supplier_assistant.knowledge import postgres_http

PATH = "/internal/v1/tools/retrieve-knowledge"
HEADERS = {"Idempotency-Key": "pg-rag-test", "X-Job-Context": str(uuid4())}


class Service:
    async def search(self, request, scope):
        await scope()
        return {
            "query": request.query,
            "chunks": [],
            "index_version": "pgvector:test",
            "warnings": [],
        }


def application(monkeypatch):
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            rag_postgres_enabled=True,
            database_url="postgresql+asyncpg://test@localhost/test",
            docs_enabled=True,
        )
    )
    app.state.resources = SimpleNamespace(sessions=object())
    app.state.rag_postgres_service = Service()

    async def identity():
        return VerifiedIdentity("https://identity.test", "verified-service", account_type="service")

    async def scope(sessions, identity, task_id, supplier_id):
        assert identity.subject == "verified-service"
        assert str(task_id) == HEADERS["X-Job-Context"]
        return SimpleNamespace()

    async def unused_request_session():
        yield None

    app.dependency_overrides[get_session] = unused_request_session
    app.dependency_overrides[authenticated_identity] = identity
    monkeypatch.setattr(postgres_http, "job_scope", scope)
    return app


@pytest.mark.asyncio
async def test_postgres_endpoint_uses_verified_job_context_and_contract(monkeypatch):
    app = application(monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.post(PATH, headers=HEADERS, json={"query": "giao trễ như thế nào?"})
    assert response.status_code == 200
    validate_dto(response.json(), "RetrievalResponse")
    schema = app.openapi()
    operation = schema["paths"][PATH]["post"]
    assert operation["security"] == [{"ServiceAuth": [], "JobContext": []}]
    assert any(p["name"] == "X-Job-Context" and p["required"] for p in operation["parameters"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Idempotency-Key": "pg-rag-test"},
        {**HEADERS, "X-Job-Context": "arbitrary-client-context"},
        {**HEADERS, "X-User-Context": "forged-user"},
        {"X-Job-Context": HEADERS["X-Job-Context"]},
    ],
)
async def test_missing_or_ambiguous_context_is_rejected(monkeypatch, headers):
    app = application(monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.post(PATH, headers=headers, json={"query": "giao hàng"})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"query": "   "},
        {"query": "giao hàng", "as_of": "2000-01-01T00:00:00Z"},
        {"query": "giao hàng", "department_id": str(uuid4())},
    ],
)
async def test_client_cannot_inject_cutoff_or_department(monkeypatch, body):
    app = application(monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.post(PATH, headers=HEADERS, json=body)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_service_role_revocation_remains_forbidden(monkeypatch):
    app = application(monkeypatch)

    async def revoked(*args):
        raise CoreError(ErrorCode.FORBIDDEN)

    monkeypatch.setattr(postgres_http, "job_scope", revoked)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.post(PATH, headers=HEADERS, json={"query": "giao hàng"})
    assert response.status_code == 403


def test_postgres_rag_requires_database_and_conflicts_with_fixture_mode():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_postgres_enabled=True)
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            rag_postgres_enabled=True,
            rag_dev_enabled=True,
            database_url="postgresql+asyncpg://test@localhost/test",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_failure", [False, True])
async def test_postgres_rag_and_ml_share_lifespan_and_cleanup(monkeypatch, schema_failure):
    from flowbridge import main
    from flowbridge.supplier_assistant.knowledge import (
        embeddings,
        postgres_service,
        postgres_store,
    )
    from flowbridge.supplier_assistant.prediction import model

    engine = SimpleNamespace(dispose=AsyncMock())
    sessions = object()
    runtime = SimpleNamespace(load=Mock())
    store = SimpleNamespace(verify_schema=AsyncMock())
    service = SimpleNamespace(close=Mock())
    if schema_failure:
        store.verify_schema.side_effect = RuntimeError("schema probe failed")
    monkeypatch.setattr(main, "create_engine", lambda settings: engine)
    monkeypatch.setattr(main, "create_session_factory", lambda engine: sessions)
    monkeypatch.setattr(main, "probe_connection", AsyncMock())
    monkeypatch.setattr(model, "TabICLRuntime", lambda settings: runtime)
    monkeypatch.setattr(postgres_store, "PgKnowledgeStore", lambda sessions: store)
    monkeypatch.setattr(embeddings, "QwenEmbedder", lambda cache: object())
    monkeypatch.setattr(postgres_service, "PgKnowledgeService", lambda store, embedder: service)
    app = create_app(
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://test@localhost/test",
            rag_postgres_enabled=True,
            ml_enabled=True,
        )
    )
    if schema_failure:
        with pytest.raises(RuntimeError, match="PostgreSQL knowledge schema check failed"):
            async with app.router.lifespan_context(app):
                pytest.fail("An invalid knowledge schema must prevent startup")
        service.close.assert_not_called()
    else:
        async with app.router.lifespan_context(app):
            assert app.state.ml_runtime is runtime
            assert app.state.rag_postgres_service is service
            assert app.state.resources.sessions is sessions
            assert not app.state.resources.http.is_closed
        service.close.assert_called_once()
    runtime.load.assert_called_once()
    store.verify_schema.assert_awaited_once()
    engine.dispose.assert_awaited_once()
    assert app.state.resources is None
    assert app.state.ml_runtime is None
    assert app.state.rag_postgres_service is None
