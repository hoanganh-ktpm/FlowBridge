# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from workspace_app.changes import change_manifest
from workspace_app.runtime import PREFIX, create_app


class Sources:
    def __init__(self, ctx):
        self.ctx, self.calls = ctx, []
        self.token, self.failure, self.malformed = "a" * 64, None, False
        self.products = [{"id": "product-1", "contract_version_id": "contract-1"}]

    async def context(self, token):
        if token != "caller":
            raise HTTPException(401)
        return self.ctx

    async def catalog(self, ctx):
        return self.products

    async def decision_events(self, ctx, producer, body):
        self.calls.append((producer, body))
        if self.failure:
            raise HTTPException(self.failure)
        return {
            "source_revision": None
            if self.malformed
            else {
                "org_id": body["org_id"],
                "warehouse_id": body["warehouse_id"],
                "producer": producer,
                "token": self.token,
            }
        }


@pytest.fixture
def setup():
    ctx = SimpleNamespace(org_id=uuid4(), owner_id=uuid4(), admin=False)
    warehouse = uuid4()
    bindings = {
        str(ctx.owner_id): {
            "org_id": str(ctx.org_id),
            "warehouse_ids": [str(warehouse)],
            "roles": ["coordinator"],
        }
    }
    return ctx, Sources(ctx), SimpleNamespace(), bindings, warehouse


@pytest.mark.asyncio
async def test_freshness_checks_only_assigned_sources_and_no_business_rows(setup):
    ctx, sources, repo, bindings, warehouse = setup
    first = await change_manifest(ctx, sources, repo, bindings)
    assert first["complete"] and len(first["versions"]) == 3
    assert {p for p, _ in sources.calls} == {"sales", "warehouse", "logistics"}
    assert all(b["warehouse_id"] == str(warehouse) and b["limit"] == 1 for _, b in sources.calls)
    sources.token = "b" * 64
    changed = await change_manifest(ctx, sources, repo, bindings)
    assert first["authorization_stamp"] == changed["authorization_stamp"]
    assert first["versions"] != changed["versions"]
    sources.products = []
    revoked = await change_manifest(ctx, sources, repo, bindings)
    assert revoked["authorization_stamp"] != first["authorization_stamp"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [403, 503, "malformed"])
async def test_partial_or_invalid_scope_is_never_a_trusted_cache_marker(setup, failure):
    ctx, sources, repo, bindings, _ = setup
    if failure == "malformed":
        sources.malformed = True
    else:
        sources.failure = failure
    result = await change_manifest(ctx, sources, repo, bindings)
    assert not result["complete"] and len(result["unavailable"]) == 3


@pytest.mark.asyncio
async def test_no_binding_or_other_organization_never_scans_sources(setup):
    ctx, sources, repo, bindings, _ = setup
    for config in (
        {},
        {str(ctx.owner_id): {**bindings[str(ctx.owner_id)], "org_id": str(uuid4())}},
    ):
        result = await change_manifest(ctx, sources, repo, config)
        assert not result["enabled"] and result["versions"] == []
    assert sources.calls == []


@pytest.mark.asyncio
async def test_http_freshness_requires_current_identity_and_is_private(setup):
    ctx, sources, repo, bindings, _ = setup
    app = create_app(sources, repo, decision_bindings=bindings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://workspace"
    ) as client:
        assert (await client.get(PREFIX + "/changes")).status_code == 401
        response = await client.get(PREFIX + "/changes", headers={"Authorization": "Bearer caller"})
        assert response.status_code == 200 and response.json()["principal_id"] == str(ctx.owner_id)
        assert response.headers["Cache-Control"] == "private, no-store"
        sources.failure = 401
        assert (
            await client.get(PREFIX + "/changes", headers={"Authorization": "Bearer caller"})
        ).status_code == 401
