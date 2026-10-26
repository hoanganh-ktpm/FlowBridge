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
        self.last_orders = {}

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
            self.last_orders[ctx.org_id] = [row]
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
    forecaster = object()

    async def plan(body, scoped, *args):
        assert body.forecast_model == "chronos2"
        assert args[-1] is forecaster
        if scoped.org_id == other:
            raise HTTPException(
                422, {"code": "INSUFFICIENT_HISTORY", "message": "Cần ít nhất 14 kỳ."}
            )
        return {
            "as_of": "2026-10-26T10:00:00+07:00",
            "products": [{"sku": "SAME-SKU", "shortage": 3}],
            "order_supply": {
                # Order IDs are opaque and must match within this scoped read.
                row["order_id"]: {
                    "status": "risk",
                    "shortages": [{"sku": "SAME-SKU", "uom": "cái", "missing_quantity": 2}],
                }
                for row in b.last_orders.get(scoped.org_id, [])
            },
        }, []

    monkeypatch.setattr("workspace_app.warehouse_overview.planning_report", plan)
    result = await warehouse_overview(
        WarehouseOverviewRequest(), ctx, b, object(), DEFAULT_SOURCE_NAMES, forecaster
    )
    assert result["warehouse_count"] == 2
    first, second = result["items"]
    assert first["organization_id"] == str(org) and second["organization_id"] == str(other)
    assert first["products"][0]["available"] == second["products"][0]["available"] == 10
    assert first["products"][0]["shortage"] == 3 and second["products"][0]["shortage"] is None
    assert first["open_orders"][0]["supply_check"]["shortages"][0]["missing_quantity"] == 2
    assert second["open_orders"][0]["supply_check"] is None
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


@pytest.mark.asyncio
async def test_inventory_returns_without_forecasting_and_never_labels_pending_as_zero(monkeypatch):
    org = uuid4()
    monkeypatch.delenv("FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS", raising=False)
    b = Boundary([org])

    async def unexpected(*args):
        pytest.fail("The initial inventory read must not call forecasting")

    monkeypatch.setattr("workspace_app.warehouse_overview.planning_report", unexpected)
    result = await warehouse_overview(
        WarehouseOverviewRequest(include_planning=False),
        Context("caller", org, uuid4(), True),
        b,
        object(),
        DEFAULT_SOURCE_NAMES,
    )
    item = result["items"][0]
    assert item["planning_status"] == "pending" and item["planning_issue"] is None
    assert item["products"][0]["available"] == 10
    assert item["products"][0]["shortage"] is None
    assert item["analysis"] is None
    assert len(item["open_orders"]) == 1
    assert len(b.reads) == 4 and len(b.checked) == 4


@pytest.mark.asyncio
async def test_background_plan_resolves_warehouse_in_reviewed_directory_only(monkeypatch):
    import json

    org, other = uuid4(), uuid4()
    monkeypatch.setenv("FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS", json.dumps([str(other)]))
    b = Boundary([org, other])
    planned = []

    async def plan(body, scoped, *args):
        planned.append(scoped.org_id)
        assert body.preparation is not None
        assert body.receipts is None and body.schedule is None and body.capacity is None
        return {
            "as_of": "2026-10-26T10:00:00+07:00",
            "products": [{"sku": "SAME-SKU", "shortage": 3}],
        }, []

    monkeypatch.setattr("workspace_app.warehouse_overview.planning_report", plan)
    ctx = Context("caller", org, uuid4(), True)
    result = await warehouse_overview(
        WarehouseOverviewRequest(warehouse_id=b.warehouses[other]),
        ctx,
        b,
        object(),
        DEFAULT_SOURCE_NAMES,
    )
    assert planned == [other] and result["warehouse_count"] == 1
    assert result["items"][0]["organization_id"] == str(other)
    assert [role for scope, role in b.reads if scope == org] == ["records_warehouses"]
    with pytest.raises(HTTPException) as error:
        await warehouse_overview(
            WarehouseOverviewRequest(warehouse_id=uuid4()),
            ctx,
            b,
            object(),
            DEFAULT_SOURCE_NAMES,
        )
    assert error.value.status_code == 404 and planned == [other]


@pytest.mark.asyncio
async def test_analysis_adds_receipt_source_only_for_the_requested_authorized_warehouse(
    monkeypatch,
):
    import json

    org, other = uuid4(), uuid4()
    monkeypatch.setenv("FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS", json.dumps([str(other)]))
    b = Boundary([org, other])

    async def plan(body, scoped, *args):
        assert scoped.org_id == other and body.warehouse_id == b.warehouses[other]
        assert body.receipts is not None and body.schedule is None and body.capacity is None
        assert body.sku == "SAME-SKU" and body.history_days == 90
        assert body.history_frequency == "week" and body.horizon == 14
        return {
            "warehouse_id": str(body.warehouse_id),
            "as_of": "2026-10-26T10:00:00+07:00",
            "selected_sku": body.sku,
            "horizon": body.horizon,
            "products": [{"sku": "SAME-SKU", "shortage": 3}],
            "history": {
                "frequency": "week",
                "receipts_available": False,
                "series": [{"day": "2026-10-19", "confirmed": 12, "received": None}],
            },
            "baseline": {"series": []},
            "explanations": {},
        }, []

    monkeypatch.setattr("workspace_app.warehouse_overview.planning_report", plan)
    request = WarehouseOverviewRequest(
        warehouse_id=b.warehouses[other],
        include_analysis=True,
        analysis_sku="SAME-SKU",
        horizon=14,
        history_days=90,
        history_frequency="week",
    )
    ctx = Context("caller", org, uuid4(), True)
    result = await warehouse_overview(request, ctx, b, object(), DEFAULT_SOURCE_NAMES)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["organization_id"] == str(other)
    analysis = item["analysis"]
    assert analysis["warehouse_id"] == str(b.warehouses[other])
    assert analysis["history_days"] == 90 and analysis["selected_sku"] == "SAME-SKU"
    assert analysis["history"]["series"][0]["received"] is None
    assert [role for scope, role in b.reads if scope == org] == ["records_warehouses"]
    b.revoke = other
    with pytest.raises(HTTPException) as denied:
        await warehouse_overview(request, ctx, b, object(), DEFAULT_SOURCE_NAMES)
    assert denied.value.status_code == 403


@pytest.mark.parametrize(
    "settings",
    [
        {"include_analysis": True},
        {"include_order_analysis": True},
        {"include_order_analysis": True, "include_planning": False, "warehouse_id": uuid4()},
        {"include_analysis": True, "include_planning": False, "warehouse_id": uuid4()},
        {"history_days": 45},
        {"history_frequency": "month"},
    ],
)
def test_invalid_analysis_scope_and_history_settings_are_rejected(settings):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WarehouseOverviewRequest.model_validate(settings)


@pytest.mark.asyncio
async def test_order_analysis_reads_transport_only_when_requested_and_survives_missing_forecasts(
    monkeypatch,
):
    from datetime import UTC, datetime

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 26, 3, tzinfo=UTC)

    monkeypatch.setattr("workspace_app.warehouse_overview.datetime", Clock)
    org = uuid4()
    monkeypatch.delenv("FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS", raising=False)
    b = Boundary([org])
    calls = []

    async def plan(body, scoped, *args):
        calls.append(body)
        assert body.schedule and body.capacity and body.receipts is None
        raise HTTPException(422, {"code": "INSUFFICIENT_HISTORY", "message": "Chưa đủ lịch sử."})

    monkeypatch.setattr("workspace_app.warehouse_overview.planning_report", plan)
    request = WarehouseOverviewRequest(warehouse_id=b.warehouses[org], include_order_analysis=True)
    ctx = Context("caller", org, uuid4(), True)
    result = await warehouse_overview(request, ctx, b, object(), DEFAULT_SOURCE_NAMES)
    analysis = result["items"][0]["order_analysis"]
    assert calls and analysis["assessment_available"] is False
    assert analysis["orders"][0]["classification"] == "unknown"
    b.revoke = org
    with pytest.raises(HTTPException) as denied:
        await warehouse_overview(request, ctx, b, object(), DEFAULT_SOURCE_NAMES)
    assert denied.value.status_code == 403
