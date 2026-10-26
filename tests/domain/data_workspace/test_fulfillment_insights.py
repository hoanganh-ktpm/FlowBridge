# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from workspace_app.fulfillment_insights import fulfillment_insights, pickup_check, project_joint
from workspace_app.runtime import PREFIX, create_app
from workspace_app.warehouse_overview import WarehouseOverviewRequest

from .test_api import RepositoryBoundary
from .test_decision_workspace import PlanningBoundary

START = date(2026, 10, 26)
NOW = datetime(2026, 10, 26, tzinfo=UTC)


def at(days, hour=10):
    return NOW + timedelta(days=days, hours=hour)


def stock(sku, available, uom="cái"):
    return dict(sku=sku, uom=uom, available_quantity=available)


def inbound(sku, quantity, when, identifier="receipt-1", status="APPROVED"):
    return dict(
        sku=sku,
        uom="cái",
        remaining_quantity=quantity,
        expected_at=when,
        inbound_plan_id=identifier,
        status=status,
    )


def order(identifier, lines, deadline):
    return dict(
        id=identifier,
        number=identifier,
        lines={(s, "cái"): q for s, q in lines},
        deadline=deadline,
        handoff_deadline=deadline + timedelta(hours=1),
    )


def report(stocks, inbounds, orders, forecasts=None, delay=2, plan_id=None, policy=True):
    if forecasts is None:
        forecasts = {row["sku"]: [dict(timestamp=str(START), p50=0, p90=0)] for row in stocks}
    headers = {
        o["id"]: dict(order_number=o["number"], requested_handoff_at=o["handoff_deadline"])
        for o in orders
    }
    return fulfillment_insights(
        stocks=stocks,
        inbounds=inbounds,
        known=orders,
        forecasts=forecasts,
        headers=headers,
        policy={"minutes": 60} if policy else None,
        start=START,
        horizon=7,
        snapshot=NOW,
        products=[],
        schedules=[],
        capacity=None,
        forecast_method="chronos2",
        delay_days=delay,
        plan_id=plan_id,
    )


def test_shared_supply_is_not_reused_for_multiple_orders_and_links_remain_explainable():
    result = report(
        [stock("A", 5)],
        [inbound("A", 5, at(1))],
        [
            order("first", [("A", 5)], at(0)),
            order("second", [("A", 5)], at(2)),
        ],
    )
    first, second = result["orders"]
    assert first["base"]["material_ready_at"] == NOW.isoformat()
    assert second["base"]["material_ready_at"] == at(1).isoformat()
    assert first["supply_links"][0]["source_id"] == "stock"
    assert second["supply_links"][0]["source_id"] == "receipt-1"
    assert second["newly_late"] and not first["affected_by_delay"]


def test_whole_order_needs_all_skus_and_exact_timestamp_plus_preparation_is_used():
    due = at(1)
    result = report(
        [stock("A", 10), stock("B", 0)],
        [inbound("B", 3, due)],
        [order("multi", [("A", 5), ("B", 3)], due)],
    )
    row = result["orders"][0]
    assert row["base"]["preparation_complete_at"] == (due + timedelta(hours=1)).isoformat()
    assert row["base"]["slack_hours"] == 0
    assert row["base"]["state"] == "on_time"


def test_only_selected_inbound_is_delayed_and_late_original_arrivals_are_never_available():
    stocks = [stock("A", 0), stock("B", 0)]
    supply = [
        inbound("A", 2, at(1), "a"),
        inbound("B", 2, at(1), "b"),
        inbound("A", 1000, NOW - timedelta(days=1), "old"),
    ]
    result = report(
        stocks, supply, [order("a", [("A", 2)], at(2)), order("b", [("B", 2)], at(2))], plan_id="a"
    )
    a, b = result["orders"]
    assert a["affected_by_delay"] and not b["affected_by_delay"]
    assert not any(r["plan_id"] == "old" for r in result["inbound_options"])
    assert (
        report(stocks, supply, [order("a", [("A", 2)], at(2))], delay=0)["summary"]["affected"] == 0
    )


def test_joint_daily_forecasts_consume_each_sku_and_p90_is_only_a_stress_scenario():
    forecasts = {sku: [dict(timestamp=str(START), p50=0, p90=5)] for sku in ["A", "B"]}
    result = report(
        [stock("A", 5), stock("B", 5)],
        [inbound("A", 5, at(2))],
        [order("multi", [("A", 10), ("B", 5)], at(3))],
        forecasts,
    )
    row = result["orders"][0]
    assert row["base"]["state"] == "on_time"
    assert row["high_demand"]["state"] == "beyond_window"
    assert result["summary"]["high_demand_exposed"] == 1
    assert {r["sku"] for r in row["shortages"]} == set()


def test_missing_stock_policy_and_incomplete_forecast_are_not_false_success():
    result = report([stock("A", 20)], [], [order("missing", [("B", 1)], at(1))])
    assert not result["available"] and result["missing_stock"] == [dict(sku="B", uom="cái")]
    result = report(
        [stock("A", 20)], [], [order("o", [("A", 1)], at(1))], forecasts={}, policy=False
    )
    assert result["orders"][0]["base"]["state"] == "unknown"
    assert result["orders"][0]["missing_forecast_skus"] == ["A"]
    assert not result["capacity_available"]
    missing_forecast = report([stock("A", 20)], [], [order("o", [("A", 1)], at(1))], forecasts={})
    assert missing_forecast["summary"]["on_time"] == 0


def test_held_and_dispatched_quantity_are_not_consumed_twice_and_uom_is_not_mixed():
    result = report([stock("A", 0, "ram")], [], [])
    assert result["summary"]["total_orders"] == 0
    result = project_joint(
        [stock("A", 5, "ram")], [], [order("wrong-unit", [("A", 1)], at(1))], {}, START, 7, NOW
    )
    assert not result["available"]
    headers = {"held": dict(order_number="Held", requested_handoff_at=at(1))}
    result = fulfillment_insights(
        stocks=[stock("A", 0)],
        inbounds=[],
        known=[],
        forecasts={},
        headers=headers,
        policy={"minutes": 60},
        start=START,
        horizon=7,
        snapshot=NOW,
        products=[],
        schedules=[],
        capacity=[],
        forecast_method="chronos2",
    )
    assert result["orders"][0]["all_lines_held_or_dispatched"]
    assert result["orders"][0]["base"]["state"] == "on_time"


def test_bottleneck_counts_order_links_instead_of_summing_repeated_shortage_quantities():
    result = report(
        [stock("A", 0), stock("B", 0)],
        [],
        [
            order("one", [("A", 100), ("B", 10)], at(1)),
            order("two", [("A", 200)], at(1)),
        ],
    )
    assert result["bottlenecks"][0]["order_count"] == 2
    assert result["bottlenecks"][0]["cumulative_percent"] == pytest.approx(200 / 3)
    assert result["bottlenecks"][1]["cumulative_percent"] == 100
    assert "missing_quantity" not in result["bottlenecks"][0]


def test_request_requires_single_authorized_warehouse_and_bounded_integer_delay():
    for invalid in [
        dict(include_fulfillment_insights=True),
        dict(warehouse_id=uuid4(), insight_delay_days=15),
        dict(warehouse_id=uuid4(), insight_delay_days=2.5),
    ]:
        with pytest.raises(ValidationError):
            WarehouseOverviewRequest.model_validate(invalid)
    with pytest.raises(HTTPException) as error:
        report([stock("A", 1)], [], [], plan_id="foreign-plan")
    assert error.value.detail["code"] == "INSIGHT_PLAN_NOT_FOUND"


def test_carrier_check_requires_preparation_time_schedule_and_usable_capacity_together():
    prepared = {"state": "on_time", "preparation_complete_at": at(1, 8).isoformat()}
    shipment = {"carrier_code": "C1", "scheduled_handoff_at": at(1, 9).isoformat()}
    entry = {
        "carrier_code": "C1",
        "capacity_date": str(at(1).date()),
        "max_dispatches": 4,
        "used_dispatches": 2,
        "status": "ACTIVE",
    }
    assert pickup_check(prepared, at(1, 10), [shipment], [entry])["state"] == "conditional"
    assert pickup_check(prepared, at(1, 10), [shipment], None)["state"] == "unknown"
    assert pickup_check(prepared, at(1, 10), [shipment, shipment], [entry])["state"] == "unknown"
    assert (
        pickup_check(prepared, at(1, 10), [shipment], [{**entry, "used_dispatches": 5}])["state"]
        == "blocked"
    )
    assert (
        pickup_check(
            prepared, at(1, 10), [{**shipment, "scheduled_handoff_at": at(1, 7)}], [entry]
        )["state"]
        == "blocked"
    )


@pytest.mark.asyncio
async def test_planning_insights_use_existing_authorized_facts_and_recheck_source_revocation():
    boundary = PlanningBoundary()
    app = create_app(boundary, RepositoryBoundary())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://workspace",
        headers={"Authorization": "Bearer caller"},
    ) as client:
        response = await client.post(
            PREFIX + "/planning",
            json={
                **boundary.refs,
                "forecast_model": "seasonal_mean",
                "include_fulfillment_insights": True,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        insights = payload["fulfillment_insights"]
        assert insights["available"] and insights["forecast_method"] == "seasonal_mean"
        assert {r["order_id"] for r in insights["orders"]} == {"order-1"}
        assert "actual_handoff_verified" not in insights["summary"]
        boundary.revoked = boundary.refs["stock"]["data_product_id"]
        response = await client.post(
            PREFIX + "/planning",
            json={
                **boundary.refs,
                "forecast_model": "seasonal_mean",
                "include_fulfillment_insights": True,
            },
        )
        assert response.status_code == 403
