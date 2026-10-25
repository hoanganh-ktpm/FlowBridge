# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from workspace_app.schemas import SimulationRequest
from workspace_app.simulation import simulate

from .test_simulation import inputs


def fixture(arrival="2026-10-25T07:30:00+07:00", quantity=30):
    body, forecast, stock, _ = inputs()
    body = body.model_copy(
        update={
            "open_orders": body.stock,
            "commitments": body.stock,
            "arrival_date": date(2026, 10, 25),
            "arrival_at": datetime.fromisoformat(arrival),
            "additional_quantity": quantity,
        }
    )
    forecast["points"] = [{**point, "p50": 0} for point in forecast["points"]]
    stock[0].update(on_hand_quantity=100, reserved_quantity=80)
    orders = [
        {
            "warehouse_id": str(body.warehouse_id),
            "order_id": "O",
            "order_line_id": "L",
            "sku": "A",
            "uom": "EA",
            "ordered_quantity": 50,
            "status": "CONFIRMED",
            "requested_handoff_at": "2026-10-25T10:00:00+07:00",
        }
    ]
    inbound = [
        {
            "warehouse_id": str(body.warehouse_id),
            "sku": "A",
            "uom": "EA",
            "status": "APPROVED",
            "remaining_quantity": 40,
            "expected_at": "2026-10-25T14:00:00+07:00",
        }
    ]
    policy = [
        {
            "warehouse_id": str(body.warehouse_id),
            "preparation_minutes": 120,
            "preparation_policy_revision": 1,
            "preparation_confirmed_by": "Kho",
            "preparation_confirmed_at": "2026-10-24T07:00:00+07:00",
        }
    ]
    return body, forecast, stock, inbound, orders, policy


def calculate(values):
    body, forecast, stock, inbound, orders, policy = values
    return simulate(
        body,
        forecast,
        stock,
        inbound,
        date(2026, 10, 24),
        orders,
        [],
        preparation_rows=policy,
        snapshot_at=datetime(2026, 10, 24, tzinfo=UTC),
    )


def test_exact_early_supply_covers_cutoff_but_larger_afternoon_supply_does_not():
    early = calculate(fixture())
    late = calculate(fixture("2026-10-25T14:00:00+07:00", 50))
    assert early["decision"]["proposed"]["peak_shortage"] == 0
    assert early["decision"]["proposed"]["known_orders_at_risk"] == 0
    assert late["decision"]["proposed"]["peak_shortage"] == 30
    assert late["decision"]["proposed"]["known_orders_at_risk"] == 1


def test_recovery_is_bounded_conditional_and_does_not_mutate_or_write_source_facts():
    values = fixture("2026-10-25T11:00:00+07:00")
    before = deepcopy(values)
    result = calculate(values)
    options = {option["kind"]: option for option in result["recovery_options"]}
    supply = options["additional_supply"]
    assert supply["quantity"] == 30
    assert supply["arrival_at"] == "2026-10-25T01:00:00+00:00"
    assert supply["projected_shortage"] == 0
    assert supply["requires_confirmation"] and supply["can_fill_simulation"]
    commitment = options["review_commitment"]
    assert commitment["suggested_handoff_at"] == "2026-10-25T06:00:00+00:00"  # 13:00 VN
    assert not commitment["can_fill_simulation"]
    assert values == before


def test_other_sku_blocking_requires_review_instead_of_recommending_more_of_a():
    values = fixture(quantity=0)
    body, forecast, stock, inbound, orders, policy = values
    orders.append({**orders[0], "order_line_id": "L-B", "sku": "B", "ordered_quantity": 1})
    stock.append({**stock[0], "sku": "B", "on_hand_quantity": 0, "reserved_quantity": 0})
    orders[0]["ordered_quantity"] = 10
    result = calculate(values)
    assert result["decision"]["stressed"]["peak_shortage"] == 0
    assert result["decision"]["stressed"]["known_orders_at_risk"] == 1
    assert [option["kind"] for option in result["recovery_options"]] == ["verify_constraints"]


def test_naive_or_mismatched_exact_arrival_is_rejected():
    values = fixture()
    with pytest.raises(ValidationError):
        SimulationRequest.model_validate(
            {**values[0].model_dump(), "arrival_at": "2026-10-25T08:00:00"}
        )
    values = (values[0].model_copy(update={"arrival_date": date(2026, 10, 26)}), *values[1:])
    with pytest.raises(HTTPException, match="Arrival timestamp and date"):
        calculate(values)


def test_exact_arrival_cannot_be_backdated_before_the_source_snapshot():
    values = fixture("2026-10-24T06:00:00+07:00")
    values = (values[0].model_copy(update={"arrival_date": date(2026, 10, 24)}), *values[1:])
    with pytest.raises(HTTPException, match="before the source snapshot"):
        calculate(values)


def test_an_already_sufficient_plan_has_no_invented_recovery_purchase():
    values = fixture(quantity=0)
    values[2][0].update(on_hand_quantity=300)
    assert calculate(values)["recovery_options"] == []


def test_source_fingerprint_includes_the_preparation_policy_revision():
    values = fixture()
    first = calculate(values)
    values[-1][0]["preparation_policy_revision"] = 2
    changed = calculate(values)
    assert first["decision"] == changed["decision"]
    assert first["evidence"]["input_sha256"] != changed["evidence"]["input_sha256"]
