# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# You may redistribute and modify it under GNU GPL version 3 or later.
# It is provided without any warranty, including MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See LICENSE for the full terms.

# Operational probes use explicit mock resources, never a real database.

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from flowbridge.main import create_app
from flowbridge.settings import Settings


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in os.environ:
        if name.startswith("FLOWBRIDGE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.mark.asyncio
async def test_liveness_and_unstarted_readiness_are_separate_from_business_schema() -> None:
    app = create_app(Settings(docs_enabled=True))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        live = await client.get("/_ops/live")
        ready = await client.get("/_ops/ready")
        schema = await client.get("/openapi.json")
    assert live.status_code == 200
    assert live.json() == {"status": "alive", "scope": "process"}
    assert ready.status_code == 503
    assert ready.json()["details"] == [{"field": "runtime", "reason": "not_started"}]
    assert schema.json()["paths"] == {}
    assert app.state.resources is None


@pytest.mark.asyncio
async def test_bootstrap_without_database_is_live_but_not_ready() -> None:
    app = create_app(Settings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            ready = await client.get("/_ops/ready")
            live = await client.get("/_ops/live")
        assert app.state.resources.engine is None
    assert live.status_code == 200
    assert ready.status_code == 503
    assert ready.json()["code"] == "UPSTREAM_UNAVAILABLE"
    assert ready.json()["retryable"] is True
    assert ready.json()["details"] == [{"field": "database", "reason": "not_configured"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ready", "failure", "timeout"])
async def test_readiness_probes_mock_database_and_releases_connection(
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(Settings(health_timeout_seconds=0.01))
    engine = MagicMock()
    connection = AsyncMock()
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    if outcome == "failure":
        connection.execute.side_effect = RuntimeError("database-secret-value")
    elif outcome == "timeout":

        async def stall(*args: object) -> None:
            await asyncio.Event().wait()

        connection.execute.side_effect = stall
    async with app.router.lifespan_context(app):
        app.state.resources = replace(app.state.resources, engine=engine, sessions=object())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/_ops/ready")
            assert (await client.get("/_ops/live")).status_code == 200
    if outcome == "ready":
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "scope": "backend_runtime"}
    else:
        assert response.status_code == 503
        assert response.json()["details"] == [
            {"field": "database", "reason": "timeout" if outcome == "timeout" else "unavailable"}
        ]
    connection.execute.assert_awaited_once()
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"
    engine.connect.return_value.__aexit__.assert_awaited_once()
    assert "secret-value" not in response.text + caplog.text


@pytest.mark.asyncio
async def test_readiness_does_not_probe_database_when_http_pool_is_closed() -> None:
    app = create_app(Settings())
    engine = MagicMock()
    async with app.router.lifespan_context(app):
        await app.state.resources.http.aclose()
        app.state.resources = replace(app.state.resources, engine=engine, sessions=object())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/_ops/ready")
    assert response.status_code == 503
    assert response.json()["details"] == [{"field": "http", "reason": "closed"}]
    engine.connect.assert_not_called()
