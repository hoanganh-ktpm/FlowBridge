# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import secrets
from uuid import uuid4

import httpx
import pytest
from scripts.langflow_test_backend import LocalProfile, create_test_app

from flowbridge.main import create_app
from flowbridge.settings import Settings


def configuration(**overrides) -> Settings:
    values = {
        "environment": "development",
        "host": "127.0.0.1",
        "port": 8001,
        "database_url": "postgresql+asyncpg://test:placeholder@localhost/test",
    }
    return Settings(_env_file=None, **{**values, **overrides})


@pytest.mark.asyncio
async def test_local_token_and_loopback_are_required_before_database_access():
    profile = LocalProfile(uuid4(), secrets.token_urlsafe(32))
    app = create_test_app(configuration(), profile)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8001") as client:
        assert (await client.get("/_ops/live")).status_code == 200
        assert (await client.get("/_ops/local-test")).status_code == 401
        assert (await client.get("/api/v1/products")).status_code == 401
        headers = {"Authorization": "Bearer " + profile.token}
        result = await client.get("/_ops/local-test", headers=headers)
        assert result.status_code == 200 and result.json()["read_only"] is True
        assert (
            await client.get("/_ops/local-test", headers={"Authorization": "Bearer bad"})
        ).status_code == 401
        duplicate = [("Authorization", headers["Authorization"])] * 2
        assert (await client.get("/_ops/local-test", headers=duplicate)).status_code == 401
    remote = httpx.ASGITransport(app=app, client=("192.0.2.1", 1234))
    async with httpx.AsyncClient(transport=remote, base_url="http://127.0.0.1:8001") as client:
        assert (
            await client.get("/_ops/live", headers={"X-Forwarded-For": "127.0.0.1"})
        ).status_code == 403


@pytest.mark.asyncio
async def test_writes_admin_rag_and_internal_ml_are_not_exposed():
    profile = LocalProfile(uuid4(), secrets.token_urlsafe(32))
    app = create_test_app(configuration(ml_enabled=True), profile)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8001",
        headers={"Authorization": "Bearer " + profile.token},
    ) as client:
        for method, path in [
            ("POST", "/api/v1/purchase-requests"),
            ("PATCH", "/api/v1/purchase-requests/" + str(uuid4())),
            ("POST", "/internal/v1/ml/purchase-orders/" + str(uuid4()) + "/predict"),
            ("POST", "/internal/v1/tools/retrieve-knowledge"),
            ("GET", "/api/v1/users"),
            ("GET", "/docs"),
            ("GET", "/openapi.json"),
        ]:
            assert (await client.request(method, path, json={})).status_code in {404, 405}
        result = await client.get("/_ops/local-test", headers={"Host": "untrusted.invalid"})
        assert result.status_code == 400


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "production"},
        {"environment": "test"},
        {"host": "0.0.0.0"},
        {"port": 8000},
    ],
)
def test_profile_rejects_nonlocal_or_nondevelopment_configuration(overrides):
    with pytest.raises(ValueError):
        create_test_app(
            configuration(**overrides), LocalProfile(uuid4(), secrets.token_urlsafe(32))
        )


def test_normal_app_keeps_authentication_and_command_routes():
    app = create_app(configuration())
    assert app.dependency_overrides == {}
    assert "post" in app.openapi()["paths"]["/api/v1/purchase-requests"]
