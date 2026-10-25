# SPDX-License-Identifier: GPL-3.0-or-later
from collections import Counter
from datetime import date

from scripts.decision_demo_data import profile


def test_demo_history_and_named_acceptance_orders_share_the_same_quantities_and_keys():
    rows, summary = profile("2026-10-26")
    assert all(value == "PASS" for value in summary["base_validation"].values())
    wh = rows["warehouse"]["warehouses"][0]
    assert wh["preparation_minutes"] == 120
    assert wh["name"] == "Kho trung tâm Bình Dương"
    products = {p["id"]: p for p in rows["warehouse"]["products"]}
    assert all("Demo" not in p["name"] and p["uom"] == "cái" for p in products.values())
    orders = {str(o["id"]): o for o in rows["sales"]["sales_orders"]}
    lines = rows["sales"]["sales_order_lines"]
    stock = {r["product_id"]: r for r in rows["warehouse"]["inventory_balances"]}
    movements = Counter()
    for row in rows["warehouse"]["stock_movements"]:
        movements[row["product_id"]] += row["quantity_delta"]
    assert all(balance["on_hand_quantity"] == movements[pid] for pid, balance in stock.items())
    for expected in summary["orders"]:
        order = orders[expected["id"]]
        original = [row for row in lines if row["order_id"] == order["id"]]
        assert len(original) == len(expected["lines"])
        for row in original:
            assert row["product_name_snapshot"] == products[row["product_ref"]]["name"]
            assert (
                row["ordered_quantity"]
                > stock[row["product_ref"]]["on_hand_quantity"]
                - stock[row["product_ref"]]["reserved_quantity"]
            )
        assert order["requested_handoff_at"].date() == date(2026, 10, 27)
    plan = next(
        r for r in rows["warehouse"]["inbound_plans"] if str(r["id"]) == summary["late_inbound_id"]
    )
    assert plan["expected_at"] > orders[summary["orders"][0]["id"]]["requested_handoff_at"]
    alternative = [
        r
        for r in rows["logistics"]["handoff_capacity_calendar"]
        if r["carrier_code"] == summary["alternative_carrier"]
    ]
    assert len(alternative) == 7 and all(r["max_dispatches"] == 5 for r in alternative)
    assert all(r["org_id"] == wh["org_id"] for r in alternative)
    assert not any(
        s["carrier_code_snapshot"] == summary["alternative_carrier"]
        for s in rows["logistics"]["shipments"]
    )
    assert profile("2026-10-26")[1] == summary
