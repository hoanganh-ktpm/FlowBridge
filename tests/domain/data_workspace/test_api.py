# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import HTTPException
from workspace_app.connector import CoreConnector
from workspace_app.repository import validate_url
from workspace_app.runtime import PREFIX, create_app

from .test_pipeline import config, rows

ORG, OWNER = uuid4(), uuid4()


class CoreBoundary:
    def __init__(self):
        self.cfg = config()
        self.allowed, self.available, self.admin = True, True, True
        self.requests = []
        self.data = [{**r, "org_id": str(ORG)} for r in rows(self.cfg)]

    def respond(self, request):
        self.requests.append(request)
        assert request.url.host == "core.test"
        assert request.headers["Authorization"] == "Bearer caller"
        if not self.available:
            return httpx.Response(503)
        if request.url.path.endswith("/me"):
            return httpx.Response(
                200,
                json={
                    "principal_status": "ACTIVE",
                    "principal_id": str(OWNER),
                    "platform_role": "PLATFORM_ADMIN" if self.admin else "NONE",
                },
            )
        if request.url.path.endswith("/authorized-data-products"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": str(self.cfg.data_product_id),
                            "contract_version_id": str(self.cfg.contract_version_id),
                            "name": "sales.confirmed_demand_daily",
                            "business_key": ["org_id", "sku", "day"],
                            "fields": [{"name": n} for n in ["org_id", "sku", "day", "quantity"]],
                        }
                    ]
                    if self.allowed
                    else []
                },
            )
        assert "/query" in request.url.path and request.method == "POST"
        if not self.allowed:
            return httpx.Response(403)
        import json

        query = json.loads(request.content)
        assert query["where"] == {"org_id": {"_eq": str(ORG)}}
        offset, limit = query["offset"], query["limit"]
        return httpx.Response(
            200,
            json={
                "rows": self.data[offset : offset + limit],
                "has_more": offset + limit < len(self.data),
            },
        )


class RepositoryBoundary:
    def __init__(self):
        self.records = {}

    async def save(self, kind, ctx, payload):
        record = {"id": str(uuid4()), **deepcopy(payload)}
        self.records[(kind, record["id"], ctx.org_id, ctx.owner_id)] = record
        return record

    async def get(self, kind, identifier, ctx):
        key = (kind, str(identifier), ctx.org_id, ctx.owner_id)
        if key not in self.records:
            raise HTTPException(404)
        return self.records[key]

    async def list(self, kind, ctx):
        return [
            v
            for (k, _, org, owner), v in self.records.items()
            if (k, org, owner) == (kind, ctx.org_id, ctx.owner_id)
        ]


@pytest.mark.asyncio
async def test_http_forecast_persists_and_revocation_blocks_saved_results():
    core, repo = CoreBoundary(), RepositoryBoundary()
    async with httpx.AsyncClient(transport=httpx.MockTransport(core.respond)) as upstream:
        connector = CoreConnector(upstream, "https://core.test", ORG)
        application = create_app(connector, repo)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(application),
            base_url="http://workspace",
            headers={"Authorization": "Bearer caller"},
        ) as client:
            result = await client.post(
                PREFIX + "/forecasts", json={"config": core.cfg.model_dump(mode="json")}
            )
            assert result.status_code == 201, result.text
            identifier = result.json()["id"]
            assert result.json()["model"]["kind"] == "statistical"
            # New application instance shares only persistence, not computed memory state.
            restarted = create_app(connector, repo)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(restarted),
                base_url="http://workspace",
                headers=client.headers,
            ) as restarted_client:
                reread = await restarted_client.get(PREFIX + "/forecasts/" + identifier)
                assert reread.json() == result.json()
            core.allowed = False
            assert (await client.get(PREFIX + "/forecasts/" + identifier)).status_code == 403
            assert (await client.get(PREFIX + "/forecasts/saved")).json()["items"] == []
            assert (
                await client.post(PREFIX + "/analytics", json=core.cfg.model_dump(mode="json"))
            ).status_code == 403
            core.available = False
            assert (await client.get(PREFIX + "/products")).status_code == 503
            assert (
                await client.get(PREFIX + "/products", headers={"Authorization": ""})
            ).status_code == 401
    assert all("hasura" not in str(r.url) for r in core.requests)
    assert all(r.method == "GET" or r.url.path.endswith("/query") for r in core.requests)


@pytest.mark.asyncio
async def test_mixed_org_and_permission_during_pagination_fail_closed():
    core = CoreBoundary()
    core.data[0]["org_id"] = str(uuid4())
    async with httpx.AsyncClient(transport=httpx.MockTransport(core.respond)) as http:
        connector = CoreConnector(http, "https://core.test", ORG)
        ctx = await connector.context("caller")
        with pytest.raises(HTTPException, match="organization"):
            await connector.rows(ctx, core.cfg)
        core.data = [{**r, "org_id": str(ORG)} for r in rows(core.cfg)] * 3
        count = 0

        def revoke(request):
            nonlocal count
            if "/query" in request.url.path:
                count += 1
                if count == 2:
                    core.allowed = False
            return core.respond(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(revoke)) as changed_http:
            with pytest.raises(HTTPException) as denied:
                await CoreConnector(changed_http, "https://core.test", ORG).rows(ctx, core.cfg)
            assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_non_it_cannot_save_config_and_owner_isolation():
    core, repo = CoreBoundary(), RepositoryBoundary()
    core.admin = False
    async with httpx.AsyncClient(transport=httpx.MockTransport(core.respond)) as http:
        connector = CoreConnector(http, "https://core.test", ORG)
        app = create_app(connector, repo)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://workspace",
            headers={"Authorization": "Bearer caller"},
        ) as client:
            result = await client.post(
                PREFIX + "/configurations", json=core.cfg.model_dump(mode="json")
            )
            assert result.status_code == 403
        ctx = await connector.context("caller")
        saved = await repo.save("forecast", ctx, {"test": True})
        from workspace_app.connector import Context

        with pytest.raises(HTTPException):
            await repo.get("forecast", UUID(saved["id"]), Context("caller", ORG, uuid4(), False))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://workspace_owner@localhost/sales_db",
        "postgresql://core_owner@localhost/dxos_core_db",
        "postgresql://workspace_owner@localhost/flowbridge_workspace_db?dbname=sales_db",
    ],
)
def test_only_workspace_database_target_is_accepted(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.asyncio
async def test_future_observations_cannot_be_forecast_context():
    core = CoreBoundary()
    today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    cfg = config(start=today + timedelta(days=1), end=today + timedelta(days=28))
    async with httpx.AsyncClient(transport=httpx.MockTransport(core.respond)) as http:
        app = create_app(CoreConnector(http, "https://core.test", ORG), RepositoryBoundary())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://workspace",
            headers={"Authorization": "Bearer caller"},
        ) as client:
            result = await client.post(
                PREFIX + "/forecasts", json={"config": cfg.model_dump(mode="json")}
            )
            assert result.status_code == 422
            assert len(core.requests) == 1  # Identity only, no data or inference access.
