# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from workspace_app.schemas import SimulationRequest
from workspace_app.simulation import project, simulate


def points():
    return [
        {
            "timestamp": (date(2026, 10, 24) + timedelta(days=i)).isoformat(),
            "item_id": "A",
            "p50": 10,
        }
        for i in range(7)
    ]


def test_unchanged_supply_conservation_and_nonnegative_physical():
    baseline = project(points(), 20, [(date(2026, 10, 26), 10)])
    assert baseline == project(points(), 20, [(date(2026, 10, 26), 10)])
    assert baseline["metrics"]["first_risk_date"] == "2026-10-27"
    assert baseline["metrics"]["peak_shortage"] == 40
    assert all(p["physical_inventory"] >= 0 for p in baseline["series"])
    assert baseline["series"][-1]["net_position"] == 20 + 10 - 70
    assert baseline["metrics"]["fulfilled_quantity"] == 30


def test_late_inbound_does_not_repair_past_shortage_and_demand_multiplier():
    early = project(points(), 20, [], extra=50, arrival=date(2026, 10, 26))
    late = project(points(), 20, [], extra=50, arrival=date(2026, 10, 29))
    baseline = project(points(), 20, [])
    assert early["metrics"]["first_risk_date"] is None
    assert late["metrics"]["first_risk_date"] == "2026-10-26"
    assert late["series"][2] == baseline["series"][2]
    assert late["metrics"]["peak_shortage"] == 30
    increased = project(points(), 20, [], multiplier=2)
    assert increased["metrics"]["peak_shortage"] > baseline["metrics"]["peak_shortage"]
    delayed = project(points(), 20, [(date(2026, 10, 26), 50)], delay=3)
    assert delayed == late


def inputs():
    reference = {"data_product_id": uuid4(), "contract_version_id": uuid4()}
    warehouse = uuid4()
    body = SimulationRequest(
        forecast_run_id=uuid4(),
        sku="A",
        warehouse_id=warehouse,
        stock=reference,
        inbound=reference,
        arrival_date="2026-10-26",
    )
    forecast = {
        "points": points(),
        "config": {
            "metric_type": "FLOW",
            "frequency": "D",
            "aggregation": "SUM",
            "entity_column": "sku",
            "unit": "EA",
            "timezone": "Asia/Ho_Chi_Minh",
        },
        "evidence": {"data_products": [{"name": "sales.confirmed_demand_daily"}]},
    }
    stocks = [
        {
            "warehouse_id": str(warehouse),
            "sku": "A",
            "uom": "EA",
            "on_hand_quantity": 100,
            "reserved_quantity": 80,
        }
    ]
    inbound = [
        {
            "warehouse_id": str(warehouse),
            "sku": "A",
            "uom": "EA",
            "status": "APPROVED",
            "expected_at": "2026-10-23T12:00:00+07:00",
            "remaining_quantity": 500,
        }
    ]
    return body, forecast, stocks, inbound


def test_reserved_protected_overdue_not_stock_and_undefined_order_lateness():
    body, forecast, stocks, inbound = inputs()
    result = simulate(body, forecast, stocks, inbound, date(2026, 10, 24))
    assert result["baseline"] == result["proposed"]
    assert result["evidence"]["opening_available"] == 20
    assert result["evidence"]["excluded_overdue_inbound_plans"] == 1
    assert result["baseline"]["metrics"]["known_orders_at_risk"] is None
    assert result["baseline"]["metrics"]["peak_shortage"] == 50
    assert stocks[0]["on_hand_quantity"] == 100  # no mutation of snapshot


def test_uom_and_historical_stock_mismatch_fail():
    body, forecast, stocks, inbound = inputs()
    with pytest.raises(HTTPException, match="today"):
        simulate(body, forecast, stocks, inbound, date(2026, 10, 25))
    stocks[0]["uom"] = "kg"
    with pytest.raises(HTTPException, match="UOM"):
        simulate(body, forecast, stocks, inbound, date(2026, 10, 24))
