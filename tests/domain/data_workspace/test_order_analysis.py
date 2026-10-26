# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime
from zoneinfo import ZoneInfo

from workspace_app.order_analysis import analyze_orders

NOW = datetime(2026, 10, 26, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))


def header(identifier, deadline="2026-10-27T17:00:00+07:00"):
    return {
        "order_id": identifier,
        "order_number": identifier,
        "deadline": deadline,
        "status": "CONFIRMED",
        "lines": [
            {"sku": "A", "uom": "cái", "ordered_quantity": 10},
            {"sku": "B", "uom": "ram", "ordered_quantity": 4},
        ],
    }


def plan():
    return {
        "as_of": NOW.isoformat(),
        "orders": [
            {
                "order_id": "SO-1",
                "at_risk": False,
                "shortages_at_deadline": [],  # Single-SKU projection missed B's risk.
                "readiness": {
                    "conditions": dict.fromkeys(
                        ("supply", "preparation", "schedule", "capacity"), "met"
                    )
                },
            }
        ],
        "order_supply": {
            "SO-1": {
                "status": "risk",
                "shortages": [{"sku": "B", "uom": "ram", "missing_quantity": 2}],
                "stock_ready_by": "2026-10-27T15:00:00+07:00",
                "preparation_minutes": 120,
            }
        },
    }


def test_multi_item_order_uses_whole_order_supply_and_never_adds_different_units():
    result = analyze_orders([header("SO-1")], plan(), [], 7, NOW)
    row = result["orders"][0]
    assert row["classification"] == "blocked" and row["at_risk"]
    assert row["readiness"]["conditions"]["supply"] == "not_met"
    assert row["shortages_at_deadline"] == [{"sku": "B", "uom": "ram", "missing_quantity": 2}]
    assert row["lines"] == header("SO-1")["lines"]
    assert "total_quantity" not in row and not row["readiness"]["actual_handoff_verified"]


def test_unknown_supply_or_transport_never_becomes_ready_and_optional_sources_are_explicit():
    data = plan()
    data["order_supply"]["SO-1"] = {"status": "clear"}
    data["orders"][0]["readiness"]["conditions"]["capacity"] = "unknown"
    data["missing_sources"] = ["capacity"]
    result = analyze_orders([header("SO-1")], data, [], 7, NOW)
    assert result["orders"][0]["classification"] == "unknown"
    assert result["missing_sources"] == ["capacity"]
    data["orders"][0]["readiness"]["conditions"]["capacity"] = "met"
    assert (
        analyze_orders([header("SO-1")], data, [], 7, NOW)["orders"][0]["classification"] == "ready"
    )
    data["order_supply"] = {}
    assert (
        analyze_orders([header("SO-1")], data, [], 7, NOW)["orders"][0]["classification"]
        == "unknown"
    )


def test_period_includes_overdue_and_last_local_day_but_excludes_later_orders():
    headers = [
        header("past", "2026-10-25T23:00:00+07:00"),
        header("last", "2026-11-01T23:59:59+07:00"),
        header("later", "2026-11-02T00:00:00+07:00"),
    ]
    result = analyze_orders(headers, None, [], 7, NOW)
    assert [o["order_id"] for o in result["orders"]] == ["past", "last"]
    assert result["outside_horizon_count"] == 1
    assert result["orders"][0]["already_overdue"]
    assert all(o["classification"] == "unknown" for o in result["orders"])
    assert not result["assessment_available"]


def test_partly_missing_shipment_times_do_not_claim_the_order_has_a_verified_schedule():
    data = plan()
    data["order_supply"]["SO-1"] = {"status": "clear"}
    data["orders"][0]["shipments"] = [
        {"scheduled_handoff_at": "2026-10-27T10:00:00+07:00"},
        {"scheduled_handoff_at": None},
    ]
    result = analyze_orders([header("SO-1")], data, [], 7, NOW)
    assert result["orders"][0]["classification"] == "unknown"
    assert result["orders"][0]["readiness"]["conditions"]["schedule"] == "unknown"
