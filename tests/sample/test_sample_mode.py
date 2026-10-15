# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Test chế độ mẫu: bật ở test/dev, chặn ở production và thiếu DB.

from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from flowbridge import main
from flowbridge.data.adapter import SampleDataAdapter
from flowbridge.main import create_app
from flowbridge.settings import Settings


@pytest.mark.parametrize(
    "configuration",
    [
        {"environment": "production", "sample_mode": True},
        {
            "environment": "production",
            "sample_mode": True,
            "database_url": "postgresql+asyncpg://user:secret@localhost/test",
        },
        {"sample_mode": True, "database_url": "postgresql+asyncpg://user:secret@localhost/test"},
        {"sample_mode": True, "sample_upload_max_bytes": 0},
    ],
)
def test_sample_rejects_production_database_and_invalid_limits(configuration: dict) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **configuration)


@pytest.mark.asyncio
async def test_sample_is_explicit_and_disabled_by_default() -> None:
    app = create_app(Settings(_env_file=None, sample_mode=False, database_url=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/core/identity/me")).status_code == 404
    assert not hasattr(app.state, "sample_context")


@pytest.mark.asyncio
async def test_sample_allows_no_database_or_outbound_http(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = Mock(side_effect=AssertionError("unexpected engine"))
    outbound = AsyncMock(side_effect=AssertionError("unexpected upstream"))
    monkeypatch.setattr(main, "create_engine", engine)
    monkeypatch.setattr(httpx.AsyncClient, "send", outbound)
    app = create_app(Settings(_env_file=None, sample_mode=True, database_url=None))
    async with app.router.lifespan_context(app):
        assert app.state.resources.engine is None and app.state.resources.sessions is None
        assert app.state.data_service.adapter.records()[0].id == "REC-SAMPLE"
        assert app.state.identity_service.config().web_client_id == "sample-web"
    engine.assert_not_called()
    outbound.assert_not_called()


@pytest.mark.asyncio
async def test_upload_limit_and_readiness_remain_honest() -> None:
    app = create_app(
        Settings(_env_file=None, sample_mode=True, database_url=None, sample_upload_max_bytes=4)
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/core/files",
                headers={"Idempotency-Key": "small-limit"},
                files={"file": ("a.txt", b"12345")},
            )
            assert response.status_code == 422
            live = await client.get("/_ops/live")
            ready = await client.get("/_ops/ready")
            assert live.status_code == 200
            assert ready.status_code == 503
            assert ready.json()["details"] == [{"field": "database", "reason": "not_configured"}]


def test_adapters_return_fresh_data() -> None:
    first, second = SampleDataAdapter(), SampleDataAdapter()
    changed = first.records()
    changed[0].data["name"] = "mutated"
    changed.clear()
    assert first.records()[0].data["name"] == second.records()[0].data["name"] == "Sample item"
