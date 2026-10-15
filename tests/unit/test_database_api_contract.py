# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Database routes retain their contract and authentication after retiring sample APIs.
import hashlib
from importlib.resources import files
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from flowbridge.db import get_session
from flowbridge.gateway import route_map
from flowbridge.identity.authentication import VerifiedIdentity, authenticated_identity
from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.supplier_assistant.contract import application_documents, application_operations

DSN = "postgresql+asyncpg://user:password@localhost/test"


def test_database_routes_and_gateway_cover_the_contract_with_admin_separation() -> None:
    app = create_app(Settings(_env_file=None, database_url=DSN))
    generated = FastAPI.openapi(app)
    actual = {
        (method.upper(), path, spec["operationId"])
        for path, methods in generated["paths"].items()
        if not path.startswith("/_ops/")
        for method, spec in methods.items()
    }
    expected = {
        (method, path, spec["operationId"]) for method, path, spec in application_operations()
    }
    assert len(expected) == 46
    assert actual == expected
    routes = route_map("https://api.example.invalid/")
    assert {(route.method, route.path, route.operation_id) for route in routes} == expected
    assert len(routes) == len({(route.method, route.path) for route in routes})
    assert all(route.upstream == "https://api.example.invalid" for route in routes)
    for route in routes:
        if route.path.startswith("/admin/"):
            assert route.visibility == "admin"
        elif route.path.startswith("/internal/"):
            assert route.visibility == "internal"
        else:
            assert route.visibility == "public"
        assert not route.path.startswith(("/_ops/", "/api/v1/core/", "/internal/v1/core/"))


@pytest.mark.parametrize("url", ["ftp://bad", "https://u:p@host", "https://host/admin", "x"])
def test_gateway_rejects_invalid_upstreams(url: str) -> None:
    with pytest.raises(ValueError):
        route_map(url)


def test_contract_snapshots_match_checksums_and_available_upstream() -> None:
    resources = files("flowbridge").joinpath("contracts")
    manifest = resources.joinpath("application-contracts.sha256").read_text("utf-8")
    assert len(manifest.splitlines()) == 2
    for line in manifest.splitlines():
        expected, name = line.split()
        snapshot = resources.joinpath(name).read_bytes()
        assert hashlib.sha256(snapshot).hexdigest() == expected
        upstream = Path(__file__).resolve().parents[3] / "03-api/contracts" / name
        if upstream.is_file():
            assert snapshot == upstream.read_bytes()


def test_database_docs_preserve_security_and_do_not_mutate_contracts() -> None:
    canonical = application_documents()
    app = create_app(Settings(_env_file=None, database_url=DSN, docs_enabled=True))
    schema = app.openapi()
    assert schema["security"]
    assert "x-sample-mode" not in schema
    assert "jsonSchemaDialect" not in schema
    assert schema["components"]["schemas"] == canonical[0]["components"]["schemas"]
    for method, path, spec in application_operations():
        assert schema["paths"][path][method.lower()] == spec
    assert application_documents() == canonical


@pytest.mark.asyncio
async def test_retired_sample_flag_cannot_restore_fake_identity_or_core_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLOWBRIDGE_SAMPLE_MODE", "true")
    app = create_app(Settings(_env_file=None, database_url=DSN))

    async def no_database() -> None:
        return None

    app.dependency_overrides[get_session] = no_database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/api/v1/me", "/api/v1/purchase-requests", "/admin/v1/users"):
            response = await client.get(path, headers={"X-User-Id": "admin", "X-Role": "ADMIN"})
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
        assert (await client.get("/api/v1/core/identity/me")).status_code == 404
    assert not hasattr(app.state, "sample_context")


@pytest.mark.asyncio
async def test_database_http_validation_runs_before_database_commands() -> None:
    app = create_app(Settings(_env_file=None, database_url=DSN))

    async def no_database() -> None:
        return None

    async def identity() -> VerifiedIdentity:
        return VerifiedIdentity("https://test.invalid", "test")

    app.dependency_overrides[get_session] = no_database
    app.dependency_overrides[authenticated_identity] = identity
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        malformed = await client.post(
            "/api/v1/purchase-requests",
            content="{",
            headers={"Content-Type": "application/json", "Idempotency-Key": "validation"},
        )
        assert malformed.status_code == 400
        invalid = await client.post(
            "/api/v1/purchase-requests",
            json={"items": [], "requested_by": "cannot-select-actor"},
            headers={"Idempotency-Key": "validation"},
        )
        assert invalid.status_code == 422
        assert "cannot-select-actor" not in invalid.text
        duplicate = await client.get("/api/v1/suppliers?page=1&page=2")
        assert duplicate.status_code == 422


@pytest.mark.asyncio
async def test_bootstrap_without_database_exposes_no_business_routes() -> None:
    app = create_app(Settings(_env_file=None, database_url=None, docs_enabled=True))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/purchase-requests")).status_code == 404
        assert (await client.get("/openapi.json")).json()["paths"] == {}
