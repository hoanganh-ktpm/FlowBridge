# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from workspace_app.planning import known_orders, project_orders


def line(identifier, sku, quantity, **kw):
    return {
        "order_id": "order-1",
        "order_line_id": identifier,
        "warehouse_id": "w",
        "sku": sku,
        "uom": "EA",
        "ordered_quantity": quantity,
        "status": "RESERVED",
        "requested_handoff_at": "2026-10-24T18:00:00+07:00",
        **kw,
    }


def stock(sku, available):
    return {"sku": sku, "uom": "EA", "on_hand_quantity": available + 20, "reserved_quantity": 20}


def test_backlog_excludes_dispatched_and_protected_holds():
    rows = [line("l1", "A", 15), line("l2", "B", 20)]
    facts = [
        {
            "order_id": "order-1",
            "order_line_id": "l1",
            "sku": "A",
            "uom": "EA",
            "dispatched_quantity": 5,
            "held_quantity": 4,
        },
        {
            "order_id": "order-1",
            "order_line_id": "l2",
            "sku": "B",
            "uom": "EA",
            "dispatched_quantity": 0,
            "held_quantity": 20,
        },
    ]
    orders = known_orders(rows, facts, "w", "Asia/Ho_Chi_Minh")
    assert orders[0]["lines"] == {("A", "EA"): 6}
    assert known_orders(rows, facts, "other-warehouse", "Asia/Ho_Chi_Minh") == []
    facts[0]["held_quantity"] = 20
    with pytest.raises(HTTPException, match="exceed ordered"):
        known_orders(rows, facts, "w", "Asia/Ho_Chi_Minh")


def test_whole_order_reservation_and_late_receipt_never_repair_past_deadline():
    today = date(2026, 10, 24)
    orders = known_orders([line("l1", "A", 15), line("l2", "B", 1)], [], "w", "Asia/Ho_Chi_Minh")
    points = [{"timestamp": (today + timedelta(days=i)).isoformat(), "p50": 0} for i in range(3)]
    baseline = project_orders(points, [stock("A", 20), stock("B", 0)], [], orders, "A", "EA", today)
    assert baseline["series"][0]["physical_inventory"] == 20  # no partial A reservation
    assert baseline["metrics"]["known_orders_at_risk"] == 1
    inbound = [
        {
            "sku": "B",
            "uom": "EA",
            "status": "APPROVED",
            "remaining_quantity": 1,
            "expected_at": "2026-10-26T09:00:00+07:00",
        }
    ]
    proposed = project_orders(
        points, [stock("A", 20), stock("B", 0)], inbound, orders, "A", "EA", today
    )
    assert proposed["series"][0] == baseline["series"][0]
    assert proposed["series"][2]["physical_inventory"] == 5  # all lines consumed together
    assert proposed["metrics"]["known_orders_at_risk"] == 1  # missed earlier deadline persists
    assert proposed["metrics"]["known_unallocated_demand"] == 15
    assert proposed["series"][2]["known_fulfilled_quantity"] == 15
    assert proposed["series"][2]["unmet_known_quantity"] == 0
    assert proposed["series"][0]["known_demand_due"] == 15


def test_same_day_late_inbound_misses_exact_business_deadline():
    today = date(2026, 10, 24)
    orders = known_orders([line("l1", "A", 5)], [], "w", "Asia/Ho_Chi_Minh")
    points = [{"timestamp": today.isoformat(), "p50": 0}]
    inbound = [
        {
            "sku": "A",
            "uom": "EA",
            "status": "APPROVED",
            "remaining_quantity": 5,
            "expected_at": "2026-10-24T21:00:00+07:00",
        }
    ]
    late = project_orders(points, [stock("A", 0)], inbound, orders, "A", "EA", today)
    assert late["metrics"]["known_orders_at_risk"] == 1
    assert late["series"][0]["known_fulfilled_quantity"] == 5
    inbound[0]["expected_at"] = "2026-10-24T17:00:00+07:00"
    early = project_orders(points, [stock("A", 0)], inbound, orders, "A", "EA", today)
    assert early["metrics"]["known_orders_at_risk"] == 0
