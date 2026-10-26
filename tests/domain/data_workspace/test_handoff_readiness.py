# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import UTC, date, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from workspace_app.handoff_readiness import (
    handoff_conditions,
    preparation_policy,
    shift_to_stock_ready_deadline,
)
from workspace_app.planning import known_orders, project_orders
from workspace_app.runtime import PREFIX, create_app

from .test_api import RepositoryBoundary
from .test_decision_workspace import PlanningBoundary


def policy_rows(minutes=120):
    return [
        {
            "warehouse_id": "w",
            "preparation_minutes": minutes,
            "preparation_policy_revision": 1,
            "preparation_confirmed_at": "2026-10-26T08:00:00+07:00",
            "preparation_confirmed_by": "Phụ trách kho",
        }
    ]


def test_preparation_cutoff_is_two_hours_before_handoff_and_receipts_at_cutoff_are_inclusive():
    policy = preparation_policy(policy_rows(), "w")
    orders = known_orders(
        [
            {
                "warehouse_id": "w",
                "order_id": "O",
                "order_line_id": "L",
                "sku": "K120",
                "uom": "EA",
                "status": "CONFIRMED",
                "ordered_quantity": 50,
                "requested_handoff_at": "2026-10-27T10:00:00+07:00",
            }
        ],
        [],
        "w",
        "Asia/Ho_Chi_Minh",
    )
    shifted = shift_to_stock_ready_deadline(orders, policy)
    assert shifted[0]["deadline"].isoformat() == "2026-10-27T01:00:00+00:00"
    assert orders[0]["deadline"].isoformat() == "2026-10-27T03:00:00+00:00"
    for at, risk in [("08:00:00", 0), ("08:00:01", 1), ("14:00:00", 1)]:
        result = project_orders(
            [{"timestamp": "2026-10-27", "p50": 0}],
            [{"sku": "K120", "uom": "EA", "on_hand_quantity": 100, "reserved_quantity": 80}],
            [
                {
                    "sku": "K120",
                    "uom": "EA",
                    "status": "APPROVED",
                    "remaining_quantity": 30,
                    "expected_at": f"2026-10-27T{at}+07:00",
                }
            ],
            shifted,
            "K120",
            "EA",
            date(2026, 10, 27),
            snapshot_at=datetime(2026, 10, 26, 1, tzinfo=UTC),
        )
        assert result["metrics"]["known_orders_at_risk"] == risk
        assert result["metrics"]["peak_supply_shortage"] == 30 * risk
        assert result["orders"][0]["stock_ready_by"] == "2026-10-27T01:00:00+00:00"


def test_missing_policy_is_unknown_but_confirmed_zero_is_valid():
    assert preparation_policy(None, "w") is None
    assert preparation_policy([], "w") is None
    assert preparation_policy(policy_rows(0), "w")["minutes"] == 0
    assert (
        handoff_conditions({"deadline": "x", "at_risk": False}, None, "booked", "met")["status"]
        == "unknown"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"preparation_minutes": None},
        {"preparation_confirmed_by": ""},
        {"preparation_confirmed_at": None},
        {"preparation_minutes": -1},
    ],
)
def test_invalid_owner_policy_is_never_silently_assumed_zero(change):
    rows = policy_rows()
    rows[0].update(change)
    with pytest.raises(HTTPException):
        preparation_policy(rows, "w")


def test_supply_schedule_and_carrier_capacity_are_independent_and_not_actual_handoff():
    row = {"deadline": "2026-10-27T08:00:00+07:00", "at_risk": False}
    policy = preparation_policy(policy_rows(), "w")
    assert handoff_conditions(row, policy, "booked", "met")["status"] == "conditionally_feasible"
    assert handoff_conditions(row, policy, "late", "met")["status"] == "not_met"
    assert handoff_conditions(row, policy, "booked", "unknown")["status"] == "unknown"
    assert not handoff_conditions(row, policy, "booked", "met")["actual_handoff_verified"]


@pytest.mark.asyncio
async def test_http_planning_and_scenario_share_the_owner_preparation_cutoff():
    from datetime import timedelta

    boundary = PlanningBoundary()
    reference = {"data_product_id": str(uuid4()), "contract_version_id": str(uuid4())}
    row = {**policy_rows()[0], "warehouse_id": boundary.wh}
    boundary.refs["preparation"] = reference
    boundary.products[reference["data_product_id"]] = {
        "id": reference["data_product_id"],
        "name": "warehouse.handoff_preparation_policy",
        "contract_version_id": reference["contract_version_id"],
        "business_key": ["warehouse_id"],
        "fields": [{"name": name} for name in ["org_id", *row]],
    }
    boundary.facts[reference["data_product_id"]] = [row]
    day = boundary.today + timedelta(days=1)
    stock = boundary.facts[boundary.refs["stock"]["data_product_id"]][0]
    stock.update(on_hand_quantity=100, reserved_quantity=80, available_quantity=20)
    order = boundary.facts[boundary.refs["open_orders"]["data_product_id"]][0]
    order.update(ordered_quantity=50, requested_handoff_at=f"{day}T10:00:00+07:00")
    inbound = boundary.facts[boundary.refs["inbound"]["data_product_id"]][0]
    inbound.update(remaining_quantity=40, expected_at=f"{day}T14:00:00+07:00")
    for historical in boundary.facts[boundary.refs["demand"]["data_product_id"]]:
        historical["confirmed_quantity"] = 0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(boundary, RepositoryBoundary())),
        base_url="http://workspace",
    ) as client:
        headers = {"Authorization": "Bearer caller"}
        response = await client.post(
            PREFIX + "/planning",
            headers=headers,
            json={**boundary.refs, "warehouse_id": boundary.wh, "forecast_model": "seasonal_mean"},
        )
        assert response.status_code == 200, response.text
        plan = response.json()
        actual = plan["orders"][0]
        assert actual["deadline"] == order["requested_handoff_at"]
        assert (
            datetime.fromisoformat(actual["readiness"]["stock_ready_by"]).hour == 1
        )  # UTC08:00+07
        assert actual["readiness"]["preparation_minutes"] == 120
        assert plan["products"][0]["known_order_shortage"] == 30
        scenario = await client.post(
            PREFIX + "/scenarios",
            headers=headers,
            json={
                "forecast_run_id": plan["forecast"]["id"],
                "sku": "A",
                "warehouse_id": boundary.wh,
                **{
                    key: boundary.refs[key]
                    for key in ("stock", "inbound", "open_orders", "commitments", "preparation")
                },
                "arrival_date": str(boundary.today),
                "additional_quantity": 0,
            },
        )
        assert scenario.status_code == 200, scenario.text
        result = scenario.json()
        assert result["decision"]["stressed"]["peak_shortage"] == 30
        assert result["evidence"]["preparation_policy"]["revision"] == 1
