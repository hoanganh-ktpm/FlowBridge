# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException
from workspace_app.connector import CoreConnector

from .test_api import ORG, CoreBoundary


@pytest.mark.asyncio
async def test_metadata_is_coalesced_only_within_one_request_and_rows_are_always_fresh():
    core = CoreBoundary()
    async with httpx.AsyncClient(transport=httpx.MockTransport(core.respond)) as http:
        connector = CoreConnector(http, "https://core.test", ORG)
        ctx = await connector.context("caller")
        async with connector.request_scope():
            first, second = await asyncio.gather(
                connector.rows(ctx, core.cfg), connector.rows(ctx, core.cfg)
            )
            assert first == second
            catalogs = [
                r for r in core.requests if r.url.path.endswith("/authorized-data-products")
            ]
            assert len(catalogs) == 1
            assert len([r for r in core.requests if r.url.path.endswith("/query")]) >= 4
            # A cached catalog must never override a current query denial.
            core.allowed = False
            with pytest.raises(HTTPException) as error:
                await connector.rows(ctx, core.cfg)
            assert error.value.status_code == 403
        async with connector.request_scope():
            assert await connector.catalog(ctx) == []
        assert (
            len([r for r in core.requests if r.url.path.endswith("/authorized-data-products")]) == 2
        )


@pytest.mark.asyncio
async def test_parallel_request_scopes_do_not_share_metadata():
    core = CoreBoundary()
    async with httpx.AsyncClient(transport=httpx.MockTransport(core.respond)) as http:
        connector = CoreConnector(http, "https://core.test", ORG)
        ctx = await connector.context("caller")

        async def read():
            async with connector.request_scope():
                one, two = await asyncio.gather(connector.catalog(ctx), connector.catalog(ctx))
                assert one == two
                one.clear()
                assert await connector.catalog(ctx) == two

        await asyncio.gather(read(), read())
    assert len([r for r in core.requests if r.url.path.endswith("/authorized-data-products")]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "foreign", "malformed", "truncated", "revoked", "duplicate"]
)
async def test_bounded_scan_keeps_org_scope_and_rechecks_current_grant(fault):
    core = CoreBoundary()
    scanned = []

    def respond(request):
        if not request.url.path.endswith("/scan"):
            if fault == "revoked" and request.url.path.endswith("/query"):
                return httpx.Response(403)
            return core.respond(request)
        body = json.loads(request.content)
        selected = body["selection"]
        assert body["pages"] == 4
        assert selected["where"] == {"org_id": {"_eq": str(ORG)}}
        assert {"org_id", "sku", "day"} <= set(selected["select"])
        scanned.append(selected["offset"])
        output = [
            {**core.data[0], "sku": f"SCAN-{selected['offset'] + index}"}
            for index in range(400 if len(scanned) == 1 else 5)
        ]
        page = {"rows": output, "has_more": len(scanned) == 1}
        if fault == "foreign":
            output[0]["org_id"] = "foreign-org"
        elif fault == "malformed":
            page["rows"] = {"unexpected": "shape"}
        elif fault == "truncated":
            page["rows"] = output[:2]
        elif fault == "duplicate" and len(scanned) == 2:
            output[0]["sku"] = "SCAN-0"
        return httpx.Response(200, json=page)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        connector = CoreConnector(http, "https://core.test", ORG, scan_pages=4)
        ctx = await connector.context("caller")
        if fault:
            with pytest.raises(HTTPException) as error:
                await connector.rows(ctx, core.cfg, where={"org_id": {"_eq": "attacker"}})
            assert error.value.status_code == (403 if fault == "revoked" else 422)
        else:
            _, output = await connector.rows(ctx, core.cfg)
            assert len(output) == 405
            assert scanned == [0, 400]
            assert core.requests[-1].url.path.endswith("/query")
