# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import secrets
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from scripts import langflow_test_backend as local_backend
from scripts.langflow_test_backend import LocalProfile, create_test_app

from flowbridge.db import get_session
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.knowledge import postgres_http
from flowbridge.supplier_assistant.repository import SupplierRepository

PATH = "/internal/v1/tools/retrieve-knowledge"


def application(monkeypatch):
    profile = LocalProfile(uuid4(), secrets.token_urlsafe(32), uuid4(), uuid4())
    app = create_test_app(
        Settings(
            _env_file=None,
            environment="development",
            host="127.0.0.1",
            port=8001,
            database_url="postgresql+asyncpg://test:placeholder@localhost/test",
            rag_postgres_enabled=True,
        ),
        profile,
    )

    @asynccontextmanager
    async def transaction():
        yield

    async def sessions():
        yield SimpleNamespace(begin=transaction)

    async def user(_repo, name, identifier, **kwargs):
        assert name == "users" and identifier == profile.ai_user_id
        return {
            "status": "active",
            "account_type": "service",
            "external_issuer": "https://identity.test",
            "external_subject": "local-rag-ai",
        }

    scopes = []

    async def scope(_sessions, identity, task_id, supplier_id):
        assert identity.account_type == "service" and identity.subject == "local-rag-ai"
        assert task_id == profile.job_context
        scopes.append(task_id)
        return SimpleNamespace()

    class Service:
        async def search(self, request, resolve_scope):
            await resolve_scope()
            return {
                "query": request.query,
                "chunks": [],
                "index_version": "pgvector:test",
                "warnings": [],
            }

    app.dependency_overrides[get_session] = sessions
    app.state.resources = SimpleNamespace(sessions=object())
    app.state.rag_postgres_service = Service()
    monkeypatch.setattr(SupplierRepository, "get", user)
    monkeypatch.setattr(postgres_http, "job_scope", scope)
    return app, profile, scopes


@pytest.mark.asyncio
async def test_postgres_rag_requires_token_and_real_job_context_and_uses_ai_identity(monkeypatch):
    app, profile, scopes = application(monkeypatch)
    headers = {
        "Authorization": "Bearer " + profile.token,
        "X-Job-Context": str(profile.job_context),
        "Idempotency-Key": "local-rag-test",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001"
    ) as client:
        assert (await client.post(PATH, json={"query": "hồ sơ"})).status_code == 401
        response = await client.post(PATH, headers=headers, json={"query": "hồ sơ", "top_k": 3})
        assert response.status_code == 200
        assert response.json()["index_version"] == "pgvector:test"
        assert scopes and all(item == profile.job_context for item in scopes)
        for missing in ["X-Job-Context", "Idempotency-Key"]:
            partial = {k: v for k, v in headers.items() if k != missing}
            assert (
                await client.post(PATH, headers=partial, json={"query": "hồ sơ"})
            ).status_code == 422
        assert (
            await client.post(
                PATH, headers={**headers, "X-User-Context": "forged"}, json={"query": "hồ sơ"}
            )
        ).status_code == 422
        assert (
            await client.post(
                "/internal/v1/tools/index-knowledge", headers=headers, json={"documents": []}
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_revoked_ai_or_invalid_job_remains_forbidden(monkeypatch):
    app, profile, _ = application(monkeypatch)

    async def denied(*args, **kwargs):
        raise CoreError(ErrorCode.FORBIDDEN)

    monkeypatch.setattr(postgres_http, "job_scope", denied)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8001",
        headers={
            "Authorization": "Bearer " + profile.token,
            "Idempotency-Key": "local-rag-test",
            "X-Job-Context": str(profile.job_context),
        },
    ) as client:
        assert (await client.post(PATH, json={"query": "hồ sơ"})).status_code == 403


def test_postgres_rag_requires_prepared_profile():
    settings = Settings(
        _env_file=None,
        host="127.0.0.1",
        port=8001,
        database_url="postgresql+asyncpg://test@localhost/test",
        rag_postgres_enabled=True,
    )
    with pytest.raises(ValueError, match="AI account and JobContext"):
        create_test_app(settings, LocalProfile(uuid4(), secrets.token_urlsafe(32)))


@pytest.mark.asyncio
async def test_rag_warms_model_and_enforces_read_only_before_ready(
    monkeypatch,
):
    engine = Mock()
    embedder = SimpleNamespace(query=Mock())
    cpu = AsyncMock()

    @asynccontextmanager
    async def lifespan(app):
        app.state.resources = SimpleNamespace(engine=engine)
        app.state.rag_postgres_service = SimpleNamespace(cpu=cpu, embedder=embedder)
        yield

    monkeypatch.setattr(local_backend, "create_app", lambda settings: FastAPI(lifespan=lifespan))
    settings = Settings(
        _env_file=None,
        host="127.0.0.1",
        port=8001,
        database_url="postgresql+asyncpg://test@localhost/test",
        rag_postgres_enabled=True,
    )
    profile = LocalProfile(uuid4(), secrets.token_urlsafe(32), uuid4(), uuid4())
    app = create_test_app(settings, profile)
    async with app.router.lifespan_context(app):
        engine.update_execution_options.assert_called_once_with(postgresql_readonly=True)
        cpu.assert_awaited_once_with(embedder.query, "RAG readiness")
