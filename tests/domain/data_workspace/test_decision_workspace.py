# SPDX-License-Identifier: GPL-3.0-or-later
"""Business decisions over API boundary fixtures; no business DB or live model."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import HTTPException
from workspace_app.connector import Context, CoreConnector
from workspace_app.runtime import PREFIX, create_app

from .test_api import RepositoryBoundary


class PlanningBoundary:
    def __init__(self):
        self.ctx = Context("caller", uuid4(), uuid4(), False)
        self.wh, self.other = str(uuid4()), str(uuid4())
        self.today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        self.checked, self.revoked, self.reads = [], None, []
        self.refs, self.products, self.facts = {}, {}, {}

        def at(offset, hour=9):
            return f"{self.today + timedelta(days=offset)}T{hour:02}:00:00+07:00"

        rows = {
            "demand": (
                "sales.planning_demand_daily",
                [
                    {
                        "warehouse_id": self.wh,
                        "sku": sku,
                        "uom": "EA",
                        "day": str(self.today - timedelta(days=i)),
                        "confirmed_quantity": quantity,
                    }
                    for i in range(1, 61)
                    for sku, quantity in [("A", 10), ("B", 2)]
                ],
            ),
            "stock": (
                "warehouse.stock_availability",
                [
                    {
                        "warehouse_id": self.wh,
                        "sku": sku,
                        "uom": "EA",
                        "on_hand_quantity": quantity + 5,
                        "reserved_quantity": 5,
                        "available_quantity": quantity,
                        "updated_at": at(0, 0),
                    }
                    for sku, quantity in [("A", 20), ("B", 1000)]
                ],
            ),
            "inbound": (
                "warehouse.inbound_schedule",
                [
                    {
                        "inbound_plan_id": str(uuid4()),
                        "warehouse_id": self.wh,
                        "sku": "A",
                        "uom": "EA",
                        "expected_at": at(1, 18),
                        "status": "APPROVED",
                        "remaining_quantity": 15,
                    },
                    {
                        "inbound_plan_id": str(uuid4()),
                        "warehouse_id": self.wh,
                        "sku": "A",
                        "uom": "EA",
                        "expected_at": at(0, 18),
                        "status": "CANCELLED",
                        "remaining_quantity": 9999,
                    },
                ],
            ),
            "open_orders": (
                "sales.open_orders",
                [
                    {
                        "order_id": "order-1",
                        "order_number": "SO-001",
                        "status": "CONFIRMED",
                        "warehouse_id": self.wh,
                        "order_line_id": "line-1",
                        "sku": "A",
                        "uom": "EA",
                        "ordered_quantity": 30,
                        "requested_handoff_at": at(1),
                        "confirmed_at": at(-1),
                    },
                ],
            ),
            "commitments": (
                "warehouse.order_commitments",
                [
                    {
                        "order_id": "order-1",
                        "order_line_id": "line-1",
                        "warehouse_id": self.other,
                        "sku": "A",
                        "uom": "EA",
                        "dispatched_quantity": 0,
                        "held_quantity": 30,
                    },
                ],
            ),
            "catalog": (
                "warehouse.planning_catalog",
                [
                    {
                        "warehouse_id": self.wh,
                        "product_id": str(uuid4()),
                        "sku": sku,
                        "product_name": name,
                        "uom": "EA",
                        "product_status": "ACTIVE",
                        "warehouse_code": "HN",
                        "warehouse_name": "Kho Hà Nội",
                    }
                    for sku, name in [("A", "Cà phê rang"), ("B", "Trà xanh")]
                ],
            ),
            "receipts": (
                "warehouse.receipts_daily",
                [
                    {
                        "warehouse_id": self.wh,
                        "sku": "A",
                        "uom": "EA",
                        "day": str(self.today - timedelta(days=1)),
                        "received_quantity": 50,
                    },
                    {
                        "warehouse_id": self.other,
                        "sku": "A",
                        "uom": "EA",
                        "day": str(self.today - timedelta(days=1)),
                        "received_quantity": 999,
                    },
                ],
            ),
            "schedule": (
                "logistics.handoff_schedule",
                [
                    {
                        "shipment_id": str(uuid4()),
                        "shipment_number": "SHP-001",
                        "order_id": "order-1",
                        "order_number": "SO-001",
                        "warehouse_id": self.wh,
                        "carrier_code": "C1",
                        "carrier_name": "Giao hàng Miền Bắc",
                        "planned_handoff_at": at(1),
                        "scheduled_handoff_at": at(1, 18),
                        "accepted_at": at(-1),
                        "handed_over_at": None,
                        "status": "ACCEPTED",
                    },
                ],
            ),
            "capacity": (
                "logistics.handoff_capacity",
                [
                    {
                        "carrier_code": "C1",
                        "capacity_date": str(self.today),
                        "max_dispatches": 10,
                        "used_dispatches": 12,
                        "status": "ACTIVE",
                    },
                ],
            ),
        }
        for key, (name, facts) in rows.items():
            reference = {"data_product_id": str(uuid4()), "contract_version_id": str(uuid4())}
            self.refs[key] = reference
            identifier = reference["data_product_id"]
            self.products[identifier] = {
                "id": identifier,
                "contract_version_id": reference["contract_version_id"],
                "name": name,
                "business_key": ["org_id"],
                "fields": [{"name": c} for c in ["org_id", *facts[0]]],
            }
            self.facts[identifier] = [{"org_id": str(self.ctx.org_id), **f} for f in facts]

    async def context(self, token):
        assert token == "caller"
        return self.ctx

    async def product(self, ctx, ref):
        return self.products[str(ref.data_product_id)]

    async def rows(self, ctx, ref, columns=None, where=None):
        self.reads.append(str(ref.data_product_id))
        assert ctx == self.ctx
        p = self.products[str(ref.data_product_id)]
        assert set(columns or []) <= {f["name"] for f in p["fields"]}
        return p, deepcopy(self.facts[p["id"]])

    async def check(self, ctx, ref, columns):
        self.checked.append(str(ref.data_product_id))
        if str(ref.data_product_id) == self.revoked:
            raise HTTPException(403, "Revoked during cross-source read")


async def call(boundary, **changes):
    app = create_app(boundary, RepositoryBoundary())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://workspace"
    ) as client:
        return await client.post(
            PREFIX + "/planning",
            headers={"Authorization": "Bearer caller"},
            json={**boundary.refs, "horizon": 7, "history_days": 7, **changes},
        )


@pytest.mark.asyncio
async def test_decision_report_connects_orders_stock_receipts_and_schedule_by_warehouse():
    boundary = PlanningBoundary()
    result = await call(boundary)
    assert result.status_code == 200, result.text
    report = result.json()
    a = next(p for p in report["products"] if p["sku"] == "A")
    assert a["name"] == "Cà phê rang" and a["known_quantity"] == 30
    assert a["incoming_quantity"] == 15  # cancelled plan never becomes expected supply
    assert a["orders_at_risk"] == 1  # another warehouse's hold cannot protect this order
    assert a["known_order_shortage"] == 10  # receipt at 18:00 misses the 09:00 deadline
    assert a["forecast_added_shortage"] >= 0
    assert report["kpis"]["risk_orders"] == 1
    assert report["warehouses"][0]["name"] == "Kho Hà Nội"
    assert sum(p["received"] for p in report["history"]["series"]) == 50
    assert sum(p["confirmed"] for p in report["history"]["series"]) == 70
    assert sum(p["difference"] for p in report["history"]["series"]) == -20
    assert len(report["history"]["series"]) == 7
    assert report["capacity_calendar"][0]["remaining"] == 0
    assert report["orders"][0]["schedule_status"] == "late"
    assert report["explanations"]["balance"]["formula"].startswith("Hàng đầu kỳ")
    assert {r["data_product_id"] for r in boundary.refs.values()} <= set(boundary.checked)


@pytest.mark.asyncio
async def test_calendar_handoff_plans_every_day_through_a_custom_end_date():
    response = await call(PlanningBoundary(), horizon=10)
    assert response.status_code == 200, response.text
    report = response.json()
    assert (date.fromisoformat(report["end"]) - date.fromisoformat(report["start"])).days == 9
    assert len(report["baseline"]["series"]) == 10
    assert report["baseline"]["series"][-1]["day"] == report["end"]


@pytest.mark.asyncio
async def test_explicit_history_grain_preserves_both_flow_totals():
    boundary = PlanningBoundary()
    daily = (await call(boundary, history_frequency="day")).json()["history"]
    weekly = (await call(boundary, history_frequency="week")).json()["history"]
    assert daily["frequency"] == "day" and weekly["frequency"] == "week"
    assert len(weekly["series"]) < len(daily["series"])
    for key in ("confirmed", "received"):
        assert sum(p[key] for p in daily["series"]) == sum(p[key] for p in weekly["series"])


@pytest.mark.asyncio
async def test_receipts_with_another_unit_are_not_reported_as_zero():
    boundary = PlanningBoundary()
    boundary.facts[boundary.refs["receipts"]["data_product_id"]][0]["uom"] = "BOX"
    response = await call(boundary)
    assert response.status_code == 422
    assert "UOM mismatch" in response.text


@pytest.mark.asyncio
async def test_source_bindings_accept_enterprise_names_without_relaxing_contract_or_permission(
    monkeypatch,
):
    import json

    boundary = PlanningBoundary()
    identifier = boundary.refs["demand"]["data_product_id"]
    boundary.products[identifier]["name"] = "acme.confirmed_orders_by_warehouse"
    assert (await call(boundary)).status_code == 422
    monkeypatch.setenv(
        "FLOWBRIDGE_WORKSPACE_SOURCE_NAMES",
        json.dumps(
            {
                "demand": "acme.confirmed_orders_by_warehouse",
            }
        ),
    )
    result = await call(boundary)
    assert result.status_code == 200, result.text
    assert result.json()["selected_sku"] == "A"
    boundary.revoked = identifier
    assert (await call(boundary)).status_code == 403


@pytest.mark.asyncio
async def test_enterprise_aliases_continue_from_planning_into_scenario_and_sensitivity(monkeypatch):
    import json

    boundary = PlanningBoundary()
    aliases = {key: f"acme.{key}" for key in boundary.refs}
    for key, ref in boundary.refs.items():
        boundary.products[ref["data_product_id"]]["name"] = aliases[key]
    monkeypatch.setenv("FLOWBRIDGE_WORKSPACE_SOURCE_NAMES", json.dumps(aliases))
    app = create_app(boundary, RepositoryBoundary())
    headers = {"Authorization": "Bearer caller"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://workspace"
    ) as client:
        response = await client.post(PREFIX + "/planning", headers=headers, json=boundary.refs)
        assert response.status_code == 200, response.text
        plan = response.json()
        scenario = await client.post(
            PREFIX + "/scenarios",
            headers=headers,
            json={
                "forecast_run_id": plan["forecast"]["id"],
                "warehouse_id": boundary.wh,
                "sku": "A",
                "arrival_date": plan["start"],
                "include_sensitivity": True,
                **{k: boundary.refs[k] for k in ["stock", "inbound", "open_orders", "commitments"]},
            },
        )
    assert scenario.status_code == 200, scenario.text
    assert scenario.json()["sensitivity"]["cells"]


@pytest.mark.asyncio
async def test_planning_filters_at_core_before_paginating_large_multiwarehouse_history():
    import json

    boundary = PlanningBoundary()
    demand_id = boundary.refs["demand"]["data_product_id"]
    # The unfiltered source exceeds Core's 10,000-offset budget.
    boundary.facts[demand_id].extend(
        [{**boundary.facts[demand_id][0], "warehouse_id": boundary.other}] * 11000
    )
    for key in ["commitments", "schedule"]:
        identifier = boundary.refs[key]["data_product_id"]
        boundary.facts[identifier].extend(
            [{**boundary.facts[identifier][0], "order_id": "historical-order"}] * 11000
        )
    queries, catalog_reads = [], []

    def respond(request):
        if request.url.path.endswith("/me"):
            return httpx.Response(
                200,
                json={
                    "principal_status": "ACTIVE",
                    "principal_id": str(boundary.ctx.owner_id),
                    "platform_role": "NONE",
                },
            )
        if request.url.path.endswith("/authorized-data-products"):
            catalog_reads.append(request)
            return httpx.Response(200, json={"items": list(boundary.products.values())})
        identifier = request.url.path.split("/")[-2]
        query = json.loads(request.content)
        queries.append((identifier, query))
        assert query["where"]["org_id"] == {"_eq": str(boundary.ctx.org_id)}
        rows = boundary.facts[identifier]
        for key, operators in query["where"].items():
            for operator, value in operators.items():
                if operator == "_eq":
                    rows = [r for r in rows if str(r[key]) == value]
                elif operator == "_gte":
                    rows = [r for r in rows if str(r[key]) >= value]
                elif operator == "_lt":
                    rows = [r for r in rows if str(r[key]) < value]
                elif operator == "_lte":
                    rows = [r for r in rows if str(r[key]) <= value]
                elif operator == "_in":
                    assert 1 <= len(value) <= 100
                    rows = [r for r in rows if str(r[key]) in value]
                elif operator == "_is_null":
                    rows = [r for r in rows if (r[key] is None) == value]
                else:
                    raise AssertionError(operator)
        return httpx.Response(
            200, json={"rows": rows[query["offset"] : query["offset"] + query["limit"]]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
        connector = CoreConnector(upstream, "https://core.test", boundary.ctx.org_id)
        app = create_app(connector, RepositoryBoundary())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://workspace"
        ) as client:
            result = await client.post(
                PREFIX + "/planning",
                headers={"Authorization": "Bearer caller"},
                json=boundary.refs,
            )
    assert result.status_code == 200, result.text
    assert result.json()["selected_sku"] == "A"
    assert result.json()["products"][0]["forecast_quantity"] == 70
    assert queries[0][0] == boundary.refs["stock"]["data_product_id"]
    assert len(catalog_reads) == 1  # all source reads share only request-scoped metadata
    paginated = [q for identifier, q in queries if identifier == demand_id and q["limit"] == 100]
    assert paginated and max(q["offset"] for q in paginated) < 500
    assert all(q["where"]["warehouse_id"] == {"_eq": boundary.wh} for q in paginated)
    assert all("_gte" in q["where"]["day"] and "_lt" in q["where"]["day"] for q in paginated)
    for key in ["commitments", "schedule"]:
        queries_for_role = [
            q
            for identifier, q in queries
            if identifier == boundary.refs[key]["data_product_id"] and q["limit"] == 100
        ]
        assert all(q["where"]["order_id"] == {"_in": ["order-1"]} for q in queries_for_role)


@pytest.mark.asyncio
async def test_unshared_receipts_or_schedule_remain_unknown_instead_of_zero_or_late():
    boundary = PlanningBoundary()
    result = await call(boundary, receipts=None, schedule=None)
    assert result.status_code == 200, result.text
    report = result.json()
    assert report["history"]["receipts_available"] is False
    assert all(
        p["received"] is None and p["difference"] is None for p in report["history"]["series"]
    )
    assert report["orders"][0]["schedule_status"] == "unknown"
    assert {"receipts", "schedule"} <= set(report["missing_sources"])


@pytest.mark.asyncio
async def test_cross_source_revocation_is_checked_again_before_returning_report():
    boundary = PlanningBoundary()
    boundary.revoked = boundary.refs["stock"]["data_product_id"]
    assert (await call(boundary)).status_code == 403


@pytest.mark.asyncio
async def test_foreign_warehouse_and_wrong_role_source_cannot_produce_a_report():
    boundary = PlanningBoundary()
    assert (await call(boundary, warehouse_id=boundary.other)).status_code == 422
    boundary.products[boundary.refs["demand"]["data_product_id"]]["name"] = (
        "warehouse.receipts_daily"
    )
    assert (await call(boundary)).status_code == 422


@pytest.mark.asyncio
async def test_long_history_groups_weeks_without_changing_totals():
    boundary = PlanningBoundary()
    result = await call(boundary, history_days=90)
    assert result.status_code == 200, result.text
    history = result.json()["history"]
    assert history["frequency"] == "week"
    assert sum(p["confirmed"] for p in history["series"]) == 600
    assert sum(p["received"] for p in history["series"]) == 50


@pytest.mark.asyncio
async def test_original_charts_can_read_names_without_requiring_operational_planning_sources():
    boundary = PlanningBoundary()
    demand_ref = boundary.refs["demand"]
    boundary.products[demand_ref["data_product_id"]]["name"] = "sales.confirmed_demand_daily"
    app = create_app(boundary, RepositoryBoundary())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://workspace"
    ) as client:
        response = await client.post(
            PREFIX + "/overview",
            headers={"Authorization": "Bearer caller"},
            json={**demand_ref, "days": 7, "catalog": boundary.refs["catalog"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["entity_labels"] == {"A": "Cà phê rang", "B": "Trà xanh"}
        assert response.json()["analytics"]["metrics"][0]["total"] == 70
        assert boundary.refs["catalog"]["data_product_id"] in boundary.checked
        # Names that disagree across authorized warehouses must not be picked at random.
        catalog_id = boundary.refs["catalog"]["data_product_id"]
        boundary.facts[catalog_id].append(
            {
                **boundary.facts[catalog_id][0],
                "product_name": "Tên khác",
                "warehouse_id": boundary.other,
            }
        )
        response = await client.post(
            PREFIX + "/overview",
            headers={"Authorization": "Bearer caller"},
            json={**demand_ref, "days": 7, "catalog": boundary.refs["catalog"]},
        )
        assert "A" not in response.json()["entity_labels"]


@pytest.mark.asyncio
async def test_sensitivity_reuses_authorized_snapshot_and_serializes_without_new_fact_reads():
    boundary = PlanningBoundary()
    app = create_app(boundary, RepositoryBoundary())
    headers = {"Authorization": "Bearer caller"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://workspace"
    ) as client:
        planning = await client.post(PREFIX + "/planning", headers=headers, json=boundary.refs)
        assert planning.status_code == 200, planning.text
        plan = planning.json()
        body = {
            "forecast_run_id": plan["forecast"]["id"],
            "warehouse_id": boundary.wh,
            "sku": plan["selected_sku"],
            "arrival_date": plan["start"],
            **{k: boundary.refs[k] for k in ["stock", "inbound", "open_orders", "commitments"]},
        }
        initial = await client.post(PREFIX + "/scenarios", headers=headers, json=body)
        assert initial.status_code == 200, initial.text
        assert initial.json()["sensitivity"] is None
        reads = len(boundary.reads)
        body.update(
            {"snapshot_id": initial.json()["evidence"]["snapshot_id"], "include_sensitivity": True}
        )
        compared = await client.post(PREFIX + "/scenarios", headers=headers, json=body)
        assert compared.status_code == 200, compared.text
        assert 1 <= len(compared.json()["sensitivity"]["cells"]) <= 25
        assert len(boundary.reads) == reads
        assert compared.json()["baseline"] == initial.json()["baseline"]
        boundary.revoked = boundary.refs["stock"]["data_product_id"]
        assert (
            await client.post(PREFIX + "/scenarios", headers=headers, json=body)
        ).status_code == 403
