# SPDX-License-Identifier: GPL-3.0-or-later
from uuid import uuid4

import pytest
from fastapi import HTTPException
from workspace_app.connector import Context
from workspace_app.source_roles import DEFAULT_SOURCE_NAMES
from workspace_app.warehouse_overview import WarehouseOverviewRequest, warehouse_overview


class Boundary:
    def __init__(self, organizations):
        self.organizations = organizations
        self.warehouses = {org: uuid4() for org in organizations}
        self.products = [
            dict(id=str(uuid4()), contract_version_id=str(uuid4()), name=name)
            for name in DEFAULT_SOURCE_NAMES.values()
        ]
        self.roles = {
            p["id"]: role
            for role, name in DEFAULT_SOURCE_NAMES.items()
            for p in self.products
            if p["name"] == name
        }
        self.checked = []
        self.reads = []
        self.revoke = None

    async def catalog(self, ctx):
        return self.products

    async def rows(self, ctx, ref, columns):
        assert ctx.org_id in self.organizations
        role = self.roles[str(ref.data_product_id)]
        self.reads.append((ctx.org_id, role))
        wh = str(self.warehouses[ctx.org_id])
        row = {"org_id": str(ctx.org_id), "warehouse_id": wh, "sku": "SAME-SKU", "uom": "cái"}
        if role == "records_warehouses":
            row.update(warehouse_name="Kho " + wh[:4], warehouse_code=wh[:4], status="ACTIVE")
        elif role == "stock":
            row.update(on_hand_quantity=12, reserved_quantity=2, available_quantity=10)
        elif role == "catalog":
            row.update(product_name="Sản phẩm")
        elif role == "open_orders":
            row.update(
                order_id=str(uuid4()),
                order_number="SO-1",
                status="CONFIRMED",
                order_line_id=str(uuid4()),
                ordered_quantity=15,
                requested_handoff_at="2026-10-29T10:00:00+07:00",
            )
        return {}, [row]

    async def check(self, ctx, ref, columns):
        self.checked.append(ctx.org_id)
        if self.revoke == ctx.org_id:
            raise HTTPException(403, "Scope revoked")


@pytest.mark.asyncio
async def test_reviewed_scopes_keep_warehouses_separate_and_missing_forecasts_unknown(
    monkeypatch,
):
    import json

    org, other = uuid4(), uuid4()
    monkeypatch.setenv("FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS", json.dumps([str(other)]))
    b = Boundary([org, other])
    ctx = Context("caller", org, uuid4(), True)

    async def plan(body, scoped, *args):
        if scoped.org_id == other:
            raise HTTPException(
                422, {"code": "INSUFFICIENT_HISTORY", "message": "Cần ít nhất 14 kỳ."}
            )
        return {
            "as_of": "2026-10-26T10:00:00+07:00",
            "products": [{"sku": "SAME-SKU", "shortage": 3}],
        }, []

    monkeypatch.setattr("workspace_app.warehouse_overview.planning_report", plan)
    result = await warehouse_overview(
        WarehouseOverviewRequest(), ctx, b, object(), DEFAULT_SOURCE_NAMES
    )
    assert result["warehouse_count"] == 2
    first, second = result["items"]
    assert first["organization_id"] == str(org) and second["organization_id"] == str(other)
    assert first["products"][0]["available"] == second["products"][0]["available"] == 10
    assert first["products"][0]["shortage"] == 3 and second["products"][0]["shortage"] is None
    assert second["planning_status"] == "needs_data" and len(second["open_orders"]) == 1
    assert set(b.checked) == {org, other}


@pytest.mark.asyncio
async def test_ordinary_caller_cannot_expand_warehouse_scope(monkeypatch):
    org = uuid4()
    b = Boundary([org])
    with pytest.raises(HTTPException) as error:
        await warehouse_overview(
            WarehouseOverviewRequest(),
            Context("caller", org, uuid4(), False),
            b,
            object(),
            DEFAULT_SOURCE_NAMES,
        )
    assert error.value.status_code == 403 and not b.reads


@pytest.mark.asyncio
async def test_default_scope_stays_single_and_revocation_discards_all_warehouse_results(
    monkeypatch,
):
    org, other = uuid4(), uuid4()
    b = Boundary([org, other])
    ctx = Context("caller", org, uuid4(), True)

    async def plan(*args):
        raise HTTPException(422, {"code": "NO_DATA", "message": "Chưa đủ lịch sử."})

    monkeypatch.setattr("workspace_app.warehouse_overview.planning_report", plan)
    monkeypatch.delenv("FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS", raising=False)
    result = await warehouse_overview(
        WarehouseOverviewRequest(), ctx, b, object(), DEFAULT_SOURCE_NAMES
    )
    assert result["warehouse_count"] == 1 and {org_id for org_id, _ in b.reads} == {org}
    import json

    monkeypatch.setenv("FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS", json.dumps([str(other)]))
    b.revoke = other
    with pytest.raises(HTTPException) as error:
        await warehouse_overview(WarehouseOverviewRequest(), ctx, b, object(), DEFAULT_SOURCE_NAMES)
    assert error.value.status_code == 403
