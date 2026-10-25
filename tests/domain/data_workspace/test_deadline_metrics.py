# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent golden expectations for canonical API, saved and export metrics."""

from datetime import date

from workspace_app.scenario_decision import measures

from .test_ml_decisions import calculate
from .test_simulation import inputs


def test_canonical_metric_keeps_morning_deficit_in_summary_and_comparison():
    body, forecast, stocks, _ = inputs()
    body = body.model_copy(
        update={
            "open_orders": body.stock,
            "commitments": body.stock,
            "additional_quantity": 30,
            "arrival_date": date(2026, 10, 24),
        }
    )
    forecast["points"] = [{**p, "p50": 0} for p in forecast["points"]]
    orders = [
        {
            "warehouse_id": str(body.warehouse_id),
            "order_id": "O",
            "order_line_id": "L",
            "sku": "A",
            "uom": "EA",
            "ordered_quantity": 50,
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
            "remaining_quantity": 40,
            "expected_at": "2026-10-24T18:00:00+07:00",
        }
    ]
    result = calculate(body, forecast, stocks, inbound, orders, [])
    # 20 available at 09:00; 40 at 18:00 cannot undo the missed morning cutoff.
    assert result["baseline"]["metrics"]["peak_shortage"] == 0
    assert result["decision"]["stressed"]["peak_shortage"] == 30
    assert result["decision"]["proposed"]["peak_shortage"] == 0
    assert result["comparison"]["metrics_delta"]["peak_shortage"] == -30
    summary = result["evidence"]["business_summary"]
    assert (summary["shortage_before"], summary["shortage_after"], summary["reduced_shortage"]) == (
        30,
        0,
        30,
    )
    assert summary["risk_date_before"] == "2026-10-24"
    assert summary["shortage_basis"] == "event_time_physical_supply"


def test_other_sku_blocking_does_not_invent_physical_shortage_or_shortage_date():
    side = {
        "series": [
            {
                "day": "2026-10-24",
                "unmet_cumulative": 20,
                "daily_peak_supply_shortage": 0,
                "physical_inventory": 30,
            }
        ],
        "metrics": {
            "peak_shortage": 20,
            "peak_supply_shortage": 0,
            "forecast_demand": 0,
            "known_orders_at_risk": 1,
        },
    }
    result = measures(side)
    assert result["peak_shortage"] == 0
    assert result["first_shortage_date"] is None
    assert result["known_orders_at_risk"] == 1


def test_daily_only_projection_is_labeled_and_null_optional_event_metric_is_not_zero():
    side = {
        "series": [{"day": "2026-10-24", "unmet_cumulative": 12, "physical_inventory": 0}],
        "metrics": {
            "peak_shortage": 12,
            "peak_supply_shortage": None,
            "forecast_demand": 12,
            "known_orders_at_risk": None,
        },
    }
    assert measures(side)["shortage_basis"] == "day_end_supply"
    assert measures(side)["peak_shortage"] == 12
    assert measures(side)["known_orders_at_risk"] is None
