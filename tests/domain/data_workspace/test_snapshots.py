# SPDX-License-Identifier: GPL-3.0-or-later
from uuid import uuid4

import pytest
from fastapi import HTTPException
from workspace_app.connector import Context
from workspace_app.planning import SnapshotCache
from workspace_app.schemas import SimulationRequest


def test_snapshot_is_bounded_owner_bound_and_expires():
    ref = {"data_product_id": uuid4(), "contract_version_id": uuid4()}
    request = SimulationRequest(
        forecast_run_id=uuid4(),
        sku="A",
        warehouse_id=uuid4(),
        stock=ref,
        inbound=ref,
        arrival_date="2026-10-24",
    )
    ctx = Context("caller", uuid4(), uuid4(), False)
    cache = SnapshotCache(ttl=120, maximum=1)
    identifier = cache.put(ctx, request, {"opening": 20})
    request.snapshot_id = identifier
    assert cache.get(ctx, request) == {"opening": 20}
    with pytest.raises(HTTPException) as denied:
        cache.get(Context("other", ctx.org_id, uuid4(), False), request)
    assert denied.value.status_code == 409
    cache.put(ctx, request, {"opening": 30})
    assert len(cache.entries) == 1
    with pytest.raises(HTTPException):
        cache.get(ctx, request)
    expired = SnapshotCache(ttl=-1)
    request.snapshot_id = expired.put(ctx, request, {})
    with pytest.raises(HTTPException):
        expired.get(ctx, request)
