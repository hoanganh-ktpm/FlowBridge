# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from datetime import UTC, date, datetime

from workspace_app.schemas import SensitivityResult
from workspace_app.simulation import sensitivity, simulate

from .test_simulation import inputs


def calculate(body, forecast, stock, inbound, orders=None, commitments=None):
    at = datetime(2026, 10, 24, tzinfo=UTC)
    reference = simulate(
        body, forecast, stock, inbound, date(2026, 10, 24), orders, commitments, snapshot_at=at
    )
    result = sensitivity(
        body,
        forecast,
        stock,
        inbound,
        date(2026, 10, 24),
        orders,
        commitments,
        snapshot_at=at,
        reference_result=reference,
    )
    SensitivityResult.model_validate(result)
    return result, reference, at


def test_grid_uses_same_planner_and_does_not_accumulate_purchases_or_mutate_inputs():
    body, forecast, stock, inbound = inputs()
    body = body.model_copy(update={"additional_quantity": 40})
    before = deepcopy((body, forecast, stock, inbound))
    grid, baseline, at = calculate(body, forecast, stock, inbound)
    assert 1 <= len(grid["cells"]) <= 25
    assert len(grid["cells"]) == len(grid["dates"]) * len(grid["quantities"])
    assert grid["quantities"] == sorted(set(grid["quantities"]))
    assert body.additional_quantity in grid["quantities"]
    assert body.arrival_date.isoformat() in grid["dates"]
    for cell in grid["cells"]:
        direct = simulate(
            body.model_copy(
                update={
                    "additional_quantity": cell["quantity"],
                    "arrival_date": date.fromisoformat(cell["arrival_date"]),
                }
            ),
            forecast,
            stock,
            inbound,
            date(2026, 10, 24),
            snapshot_at=at,
        )["proposed"]["metrics"]
        assert cell["peak_shortage"] == direct["peak_shortage"]
        assert cell["known_orders_at_risk"] is None
        if cell["quantity"] == 0:
            assert cell["peak_shortage"] == baseline["baseline"]["metrics"]["peak_shortage"]
    assert (body, forecast, stock, inbound) == before


def test_sensitivity_keeps_missed_deadlines_even_when_later_supply_covers_total():
    body, forecast, stock, _ = inputs()
    body = body.model_copy(
        update={
            "additional_quantity": 120,
            "open_orders": body.stock,
            "commitments": body.stock,
        }
    )
    orders = [
        {
            "warehouse_id": str(body.warehouse_id),
            "order_id": "order",
            "order_line_id": "line",
            "sku": "A",
            "uom": "EA",
            "ordered_quantity": 30,
            "status": "CONFIRMED",
            "requested_handoff_at": "2026-10-25T09:00:00+07:00",
        }
    ]
    grid, _, _ = calculate(body, forecast, stock, [], orders, [])
    early = next(
        c for c in grid["cells"] if c["quantity"] == 120 and c["arrival_date"] == "2026-10-24"
    )
    late = next(
        c for c in grid["cells"] if c["quantity"] == 120 and c["arrival_date"] == "2026-10-30"
    )
    assert early["peak_shortage"] == 0 and early["known_orders_at_risk"] == 0
    assert late["known_orders_at_risk"] == 1
    assert late["peak_shortage"] > 0


def test_grid_honors_high_demand_and_delay_assumptions_in_every_cell():
    body, forecast, stock, inbound = inputs()
    forecast["points"] = [{**p, "p90": 20} for p in forecast["points"]]
    body = body.model_copy(
        update={
            "demand_quantile": "p90",
            "demand_multiplier": 1.2,
            "inbound_delay_days": 2,
            "additional_quantity": 80,
        }
    )
    grid, reference, _ = calculate(body, forecast, stock, inbound)
    zero = [c for c in grid["cells"] if c["quantity"] == 0]
    assert all(
        c["peak_shortage"] == reference["baseline"]["metrics"]["peak_shortage"] for c in zero
    )
    assert reference["baseline"]["metrics"]["forecast_demand"] == 168
