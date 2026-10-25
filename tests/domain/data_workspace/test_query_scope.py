# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio

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
