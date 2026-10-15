# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# FlowBridge is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
# FlowBridge is distributed without any warranty; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE.

# Runtime isolation and cleanup; simulated failures are unit evidence only.

import os
from collections.abc import AsyncGenerator
from contextlib import aclosing, asynccontextmanager
from dataclasses import replace
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from flowbridge import main
from flowbridge.db import create_engine, create_session_factory, get_session
from flowbridge.settings import Settings


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch, tmp_path):
    for name in os.environ:
        if name.startswith("FLOWBRIDGE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.mark.asyncio
async def test_apps_own_resources_and_close_even_on_error():
    first = main.create_app(Settings())
    second = main.create_app(Settings())
    assert first.state.resources is None
    async with first.router.lifespan_context(first):
        async with second.router.lifespan_context(second):
            client = first.state.resources.http
            other_client = second.state.resources.http
            assert client is not other_client
            assert client.timeout.connect == 10
        assert other_client.is_closed
        assert not client.is_closed
    assert client.is_closed
    assert first.state.resources is None
    with pytest.raises(RuntimeError, match="during request"):
        async with first.router.lifespan_context(first):
            restarted_client = first.state.resources.http
            assert restarted_client is not client
            raise RuntimeError("during request")
    assert restarted_client.is_closed
    assert first.state.resources is None


@pytest.mark.asyncio
async def test_app_factory_and_docs_default():
    app = main.create_app(Settings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/")).status_code == 404
            assert (await client.get("/docs")).status_code == 404
            assert (await client.get("/openapi.json")).status_code == 404
    docs_app = main.create_app(Settings(docs_enabled=True))
    async with docs_app.router.lifespan_context(docs_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=docs_app), base_url="http://test"
        ) as client:
            assert (await client.get("/openapi.json")).json()["paths"] == {}


@pytest.mark.asyncio
async def test_configured_engine_and_distinct_session_objects_without_connection():
    settings = Settings(database_url="postgresql+asyncpg://user:password@127.0.0.1/database")
    engine = create_engine(settings)
    try:
        sessions = create_session_factory(engine)
        async with sessions() as first, sessions() as second:
            assert first is not second
            assert engine.pool.checkedout() == 0
            assert engine.sync_engine.hide_parameters is True
            assert not first.autoflush
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unconfigured_session_and_engine_fail_explicitly():
    app = main.create_app(Settings())
    request = Request({"type": "http", "app": app})
    with pytest.raises(RuntimeError, match="not configured"):
        create_engine(Settings())
    with pytest.raises(RuntimeError, match="session unavailable"):
        async with aclosing(get_session(request)) as dependency:
            await anext(dependency)


@pytest.mark.asyncio
async def test_database_startup_failure_is_sanitized_and_cleans_resources(monkeypatch):
    engine = MagicMock()
    engine.dispose = AsyncMock()
    engine.connect.return_value.__aenter__ = AsyncMock(
        side_effect=RuntimeError("upstream error secret-password")
    )
    monkeypatch.setattr(main, "create_engine", lambda settings: engine)
    monkeypatch.setattr(main, "create_session_factory", lambda engine: object())
    real_client = httpx.AsyncClient
    clients = []

    def record_client(**kwargs):
        client = real_client(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(main.httpx, "AsyncClient", record_client)
    app = main.create_app(
        Settings(database_url="postgresql+asyncpg://user:secret-password@localhost/database")
    )
    with pytest.raises(RuntimeError, match="PostgreSQL startup check failed") as error:
        async with app.router.lifespan_context(app):
            pytest.fail("A configured but unreachable database must prevent startup")
    assert "secret-password" not in str(error.value)
    engine.dispose.assert_awaited_once()
    assert clients[0].is_closed
    assert app.state.resources is None


@pytest.mark.asyncio
async def test_http_creation_failure_still_disposes_engine(monkeypatch):
    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr(main, "create_engine", lambda settings: engine)
    monkeypatch.setattr(main, "create_session_factory", lambda engine: object())

    def fail_client(**kwargs):
        raise RuntimeError("HTTP initialization failed")

    monkeypatch.setattr(main.httpx, "AsyncClient", fail_client)
    app = main.create_app(Settings(database_url="postgresql+asyncpg://user@localhost/database"))
    with pytest.raises(RuntimeError, match="HTTP initialization failed"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup should fail")
    engine.dispose.assert_awaited_once()
    assert app.state.resources is None


@pytest.mark.asyncio
async def test_mock_database_startup_success_disposes_pool(monkeypatch):
    engine = MagicMock()
    engine.dispose = AsyncMock()
    connection = AsyncMock()
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(main, "create_engine", lambda settings: engine)
    monkeypatch.setattr(main, "create_session_factory", lambda engine: object())
    app = main.create_app(Settings(database_url="postgresql+asyncpg://user@localhost/database"))
    async with app.router.lifespan_context(app):
        client = app.state.resources.http
        assert app.state.resources.engine is engine
        connection.execute.assert_awaited_once()
        engine.dispose.assert_not_awaited()
    assert client.is_closed
    engine.dispose.assert_awaited_once()
    assert app.state.resources is None


@pytest.mark.asyncio
async def test_mock_data_requests_have_distinct_sessions_and_close_on_error():
    app = main.create_app(Settings())
    sessions = []
    records = [{"id": "mock-001", "name": "Mock record"}]

    @asynccontextmanager
    async def mock_sessions() -> AsyncGenerator[AsyncSession, None]:
        session = AsyncMock(spec=AsyncSession)
        result = MagicMock()
        result.mappings.return_value.all.return_value = records
        session.execute.return_value = result
        sessions.append(session)
        try:
            yield session
        finally:
            await session.close()

    # Test-only routes and data; none of these endpoints enter the product app.
    @app.get("/_test/items")
    async def items(session: Annotated[AsyncSession, Depends(get_session)]):
        result = await session.execute(text("SELECT id, name FROM mock_records"))
        return result.mappings().all()

    @app.get("/_test/failure")
    async def failure(session: Annotated[AsyncSession, Depends(get_session)]):
        raise RuntimeError("request failed")

    async with app.router.lifespan_context(app):
        app.state.resources = replace(app.state.resources, sessions=mock_sessions)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            for _ in range(2):
                response = await client.get("/_test/items")
                assert response.status_code == 200
                assert response.json() == records
            assert (await client.get("/_test/failure")).status_code == 500
    assert len({id(session) for session in sessions}) == 3
    for session in sessions:
        session.close.assert_awaited_once()
        session.commit.assert_not_awaited()
