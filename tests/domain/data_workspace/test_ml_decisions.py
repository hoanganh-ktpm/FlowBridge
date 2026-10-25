# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from workspace_app.runtime import PREFIX, create_app
from workspace_app.simulation import simulate

from .test_api import RepositoryBoundary
from .test_decision_workspace import PlanningBoundary
from .test_simulation import inputs


def calculate(body, forecast, stocks, inbound, orders=None, commitments=None):
    return simulate(
        body,
        forecast,
        stocks,
        inbound,
        date(2026, 10, 24),
        orders,
        commitments,
        snapshot_at=datetime(2026, 10, 24, tzinfo=UTC),
    )


def test_three_cases_separate_model_demand_shock_and_additional_supply_without_mutating_inputs():
    body, forecast, stocks, inbound = inputs()
    body = body.model_copy(
        update={
            "demand_multiplier": 1.2,
            "additional_quantity": 70,
            "arrival_date": date(2026, 10, 24),
        }
    )
    before = deepcopy((body, forecast, stocks, inbound))
    result = calculate(body, forecast, stocks, inbound)
    decision = result["decision"]
    assert decision["reference"]["new_demand"] == 70
    assert decision["stressed"]["new_demand"] == decision["proposed"]["new_demand"] == 84
    assert decision["reference"]["peak_shortage"] == 50
    assert decision["stressed"]["peak_shortage"] == 64
    assert decision["proposed"]["peak_shortage"] == 0
    assert decision["proposed"]["ending_inventory"] == 6
    assert (body, forecast, stocks, inbound) == before


def test_minimum_quantity_and_latest_day_are_actionable_and_do_not_hide_past_shortages():
    body, forecast, stocks, inbound = inputs()
    body = body.model_copy(update={"demand_multiplier": 1.2})
    decision = calculate(body, forecast, stocks, inbound)["decision"]
    assert decision["minimum_additional_quantity"] == 64
    assert decision["latest_arrival_date"] == "2026-10-25"
    proposal = body.model_copy(
        update={"additional_quantity": 64, "arrival_date": date(2026, 10, 25)}
    )
    assert (
        calculate(proposal, forecast, stocks, inbound)["decision"]["proposed"]["peak_shortage"] == 0
    )
    less = proposal.model_copy(update={"additional_quantity": 63.999})
    assert calculate(less, forecast, stocks, inbound)["decision"]["proposed"]["peak_shortage"] > 0
    late = proposal.model_copy(update={"arrival_date": date(2026, 10, 26)})
    result = calculate(late, forecast, stocks, inbound)["decision"]
    assert result["proposed"]["ending_inventory"] == 0
    assert result["proposed"]["peak_shortage"] > 0  # a balanced end cannot repair the earlier day


def test_extra_stock_without_risk_reduction_is_not_reported_as_a_beneficial_purchase():
    body, forecast, stocks, inbound = inputs()
    stocks[0]["on_hand_quantity"] = 300
    body = body.model_copy(update={"additional_quantity": 20, "demand_multiplier": 1.2})
    decision = calculate(body, forecast, stocks, inbound)["decision"]
    assert decision["verdict"] == "extra_without_risk_reduction"
    assert decision["minimum_additional_quantity"] == 0 and decision["latest_arrival_date"] is None
    assert decision["proposed"]["ending_inventory"] - decision["stressed"]["ending_inventory"] == 20
    assert decision["stressed"]["demand_headroom_percent"] == pytest.approx(100 * (220 / 70 - 1))
    assert (
        decision["proposed"]["demand_headroom_percent"]
        > decision["stressed"]["demand_headroom_percent"]
    )


def test_same_day_late_receipt_is_counted_even_if_end_of_day_is_covered():
    body, forecast, stocks, _ = inputs()
    body = body.model_copy(update={"open_orders": body.stock, "commitments": body.stock})
    forecast["points"] = [{**p, "p50": 0} for p in forecast["points"]]
    orders = [
        {
            "warehouse_id": str(body.warehouse_id),
            "order_id": "O",
            "order_line_id": "L",
            "sku": "A",
            "uom": "EA",
            "ordered_quantity": 30,
            "status": "CONFIRMED",
            "requested_handoff_at": "2026-10-24T09:00:00+07:00",
        }
    ]
    inbound = [
        {
            "warehouse_id": str(body.warehouse_id),
            "sku": "A",
            "uom": "EA",
            "status": "APPROVED",
            "remaining_quantity": 30,
            "expected_at": "2026-10-24T18:00:00+07:00",
        }
    ]
    result = calculate(body, forecast, stocks, inbound, orders, [])
    assert result["decision"]["stressed"]["shortage_days"] == 1
    assert result["decision"]["stressed"]["peak_shortage"] == 10
    assert result["decision"]["stressed"]["ending_inventory"] == 20
    assert result["decision"]["minimum_additional_quantity"] == 10
    assert result["decision"]["latest_arrival_date"] == "2026-10-24"
    assert result["decision"]["stressed"]["demand_headroom_percent"] is None


def test_require_ml_rejects_statistics_or_org_wide_forecasts_instead_of_relabeling_them():
    body, forecast, stocks, inbound = inputs()
    body = body.model_copy(update={"require_ml": True})
    with pytest.raises(HTTPException) as error:
        calculate(body, forecast, stocks, inbound, [], [])
    assert error.value.detail["code"] == "ML_SOURCE_REQUIRED"
    forecast["model"] = {"method": "ridge_lag", "kind": "machine_learning"}
    with pytest.raises(HTTPException):
        calculate(body, forecast, stocks, inbound, [], [])  # still missing warehouse binding


def test_capacity_threshold_keeps_original_ml_basis_when_high_quantile_is_selected():
    body, forecast, stocks, inbound = inputs()
    forecast["points"] = [{**p, "p90": 20} for p in forecast["points"]]
    ordinary = calculate(body, forecast, stocks, inbound)["decision"]
    high = calculate(
        body.model_copy(update={"demand_quantile": "p90", "demand_multiplier": 1.2}),
        forecast,
        stocks,
        inbound,
    )["decision"]
    assert high["stressed"]["new_demand"] == 168 and high["reference"]["new_demand"] == 70
    assert (
        high["stressed"]["demand_headroom_percent"]
        == ordinary["stressed"]["demand_headroom_percent"]
    )


@pytest.mark.asyncio
async def test_actual_warehouse_ml_run_drives_saved_three_case_decisions_and_rechecks_permissions():
    boundary = PlanningBoundary()
    demand = boundary.facts[boundary.refs["demand"]["data_product_id"]]
    prototype = demand[0]
    demand.extend(
        {
            **prototype,
            "day": str(boundary.today - timedelta(days=i)),
            "confirmed_quantity": 10 + (150 - i) * 0.1,
        }
        for i in range(61, 151)
    )
    app = create_app(boundary, RepositoryBoundary())
    headers = {"Authorization": "Bearer caller"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://workspace"
    ) as client:
        response = await client.post(
            PREFIX + "/planning",
            headers=headers,
            json={
                **boundary.refs,
                "warehouse_id": boundary.wh,
                "sku": "A",
                "forecast_model": "ridge_lag",
            },
        )
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["forecast"]["model"]["entities"]["A"]["method"] == "ridge_lag"
        assert plan["forecast"]["config"]["filters"]["warehouse_id"] == boundary.wh
        body = {
            "forecast_run_id": plan["forecast"]["id"],
            "sku": "A",
            "warehouse_id": boundary.wh,
            "arrival_date": plan["start"],
            "demand_multiplier": 1.2,
            "inbound_delay_days": 2,
            "additional_quantity": 100,
            "require_ml": True,
            "save": True,
            **{k: boundary.refs[k] for k in ["stock", "inbound", "open_orders", "commitments"]},
        }
        response = await client.post(PREFIX + "/scenarios", headers=headers, json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["decision"]["model_method"] == "ridge_lag"
        assert result["decision"]["stressed"]["new_demand"] == pytest.approx(
            1.2 * result["decision"]["reference"]["new_demand"]
        )
        assert result["reference"] and result["id"]
        saved = await client.get(PREFIX + "/scenarios/" + result["id"], headers=headers)
        assert saved.status_code == 200 and saved.json()["decision"] == result["decision"]
        boundary.revoked = boundary.refs["stock"]["data_product_id"]
        assert (
            await client.get(PREFIX + "/scenarios/" + result["id"], headers=headers)
        ).status_code == 403


@pytest.mark.asyncio
async def test_short_warehouse_history_never_silently_falls_back_to_weekday_means():
    boundary = PlanningBoundary()  # 60 days cannot support training + two validation blocks
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(boundary, RepositoryBoundary())),
        base_url="http://workspace",
    ) as client:
        response = await client.post(
            PREFIX + "/planning",
            headers={"Authorization": "Bearer caller"},
            json={**boundary.refs, "sku": "A", "forecast_model": "ridge_lag"},
        )
    assert response.status_code == 422 and response.json()["detail"]["code"] == "ML_HISTORY_SHORT"
