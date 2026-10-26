# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import date

import pytest
from workspace_app.order_supply import order_supply_checks
from workspace_app.planning import known_orders, moment, project_orders

START = date(2026, 10, 26)
END = date(2026, 11, 1)
SNAPSHOT = moment("2026-10-26T08:00:00+07:00")
POLICY = {"minutes": 120}


def fixture(*, held=2, dispatched=3, arrival=None):
    rows = [
        dict(
            order_id="order",
            order_line_id=sku,
            order_number="SO-1",
            warehouse_id="wh",
            sku=sku,
            uom=unit,
            ordered_quantity=13,
            status="CONFIRMED",
            requested_handoff_at="2026-10-27T12:00:00+07:00",
        )
        for sku, unit in [("A", "cái"), ("B", "ram")]
    ]
    commitments = [
        {**row, "held_quantity": held, "dispatched_quantity": dispatched} for row in rows
    ]
    known = known_orders(rows, commitments, "wh", "Asia/Ho_Chi_Minh")
    from workspace_app.handoff_readiness import shift_to_stock_ready_deadline

    known = shift_to_stock_ready_deadline(known, {**POLICY, "revision": 1})
    stocks = [
        dict(sku=row["sku"], uom=row["uom"], on_hand_quantity=12, reserved_quantity=2)
        for row in rows
    ]
    inbound = (
        []
        if not arrival
        else [
            dict(sku="B", uom="ram", status="APPROVED", remaining_quantity=20, expected_at=arrival)
        ]
    )
    baselines = {
        sku: project_orders(
            [{"timestamp": "2026-10-26", "p50": demand}, {"timestamp": "2026-10-27", "p50": 0}],
            stocks,
            inbound,
            known,
            sku,
            unit,
            START,
            snapshot_at=SNAPSHOT,
        )
        for sku, unit, demand in [("A", "cái", 5), ("B", "ram", 7)]
    }
    return baselines, known, {"order": rows[0]}


def test_each_sku_uses_its_own_forecast_and_subtracts_held_and_dispatched_goods():
    baselines, known, headers = fixture()
    check = order_supply_checks(baselines, known, headers, POLICY, END, SNAPSHOT)["order"]
    assert check["status"] == "risk"
    assert check["shortages"] == [
        {"sku": "A", "uom": "cái", "missing_quantity": 3},
        {"sku": "B", "uom": "ram", "missing_quantity": 5},
    ]
    assert moment(check["stock_ready_by"]) == moment("2026-10-27T10:00:00+07:00")


@pytest.mark.parametrize("arrival,expected", [("09:00", False), ("11:00", True)])
def test_same_day_inbound_only_covers_shortage_before_preparation_cutoff(arrival, expected):
    baselines, known, headers = fixture(arrival=f"2026-10-27T{arrival}:00+07:00")
    check = order_supply_checks(baselines, known, headers, POLICY, END, SNAPSHOT)["order"]
    assert any(row["sku"] == "B" for row in check["shortages"]) is expected


def test_fully_held_or_dispatched_order_is_not_compared_against_unreserved_stock():
    baselines, known, headers = fixture(held=10, dispatched=3)
    check = order_supply_checks(baselines, known, headers, POLICY, END, SNAPSHOT)["order"]
    assert check["status"] == "clear" and not check["shortages"]


def test_partial_forecasts_never_mark_unassessed_order_clear_and_keep_known_risk():
    baselines, known, headers = fixture()
    baselines["B"] = None
    check = order_supply_checks(baselines, known, headers, POLICY, END, SNAPSHOT)["order"]
    assert check["status"] == "risk" and len(check["shortages"]) == 1
    assert any("tất cả mặt hàng" in note for note in check["notes"])
    baselines["A"] = None
    check = order_supply_checks(baselines, known, headers, POLICY, END, SNAPSHOT)["order"]
    assert check["status"] == "unknown" and check["shortages"] == []


def test_outside_horizon_missing_preparation_and_expired_cutoff_are_explicit():
    baselines, known, headers = fixture(held=10, dispatched=3)
    check = order_supply_checks(baselines, known, headers, None, END, SNAPSHOT)["order"]
    assert check["status"] == "unknown" and check["stock_ready_by"] is None
    assert any("thời gian chuẩn bị" in note for note in check["notes"])
    headers["order"]["requested_handoff_at"] = "2026-11-02T00:00:00+07:00"
    check = order_supply_checks(baselines, known, headers, POLICY, END, SNAPSHOT)["order"]
    assert check["status"] == "outside_horizon" and check["shortages"] == []
    headers["order"]["requested_handoff_at"] = "2026-10-26T09:00:00+07:00"
    check = order_supply_checks(baselines, known, headers, POLICY, END, SNAPSHOT)["order"]
    assert check["status"] == "unknown" and any("đã qua" in note for note in check["notes"])
