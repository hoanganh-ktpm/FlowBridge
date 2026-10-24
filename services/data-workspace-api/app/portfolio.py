# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit-safe Sales/Warehouse reference planning and Logistics calendar summaries.

No SKU-to-dispatch conversion, reservation, or claim of inventory feasibility.
Actual order deadlines and all-or-nothing allocation remain in the planner.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from .analytics import local_day, numeric
from .planning import moment


def supply_reference(report, stocks, inbound, days, instant):
    config = report["config"]
    if not (
        config["entity_column"] == "sku"
        and config["metric_type"] == "FLOW"
        and config["frequency"] == "D"
        and config["aggregation"] == "SUM"
    ):
        return None
    timezone = ZoneInfo(config["timezone"])
    today = instant.astimezone(timezone).date()
    until = datetime.combine(today + timedelta(days=days), time.min, timezone)
    series = report["comparison"]["series"]
    result = []
    for sku in report["comparison_entities"]:
        values = [p["target"] for p in series if p["item_id"] == sku][-14:]
        daily = mean(values)
        stock = [r for r in (stocks or []) if r["sku"] == sku]
        valid_unit = stock and all(r["uom"] == config["unit"] for r in stock)
        available = sum(numeric(r["available_quantity"]) for r in stock) if valid_unit else None
        if available is not None and available < 0:
            available = None
        planned, overdue = None, 0
        if inbound is not None and valid_unit:
            incoming = [r for r in inbound if r["sku"] == sku]
            if all(r["uom"] == config["unit"] for r in incoming):
                planned = 0
                for row in incoming:
                    if row["status"] not in {"APPROVED", "PARTIALLY_RECEIVED"}:
                        continue
                    at = moment(row["expected_at"])
                    quantity = numeric(row["remaining_quantity"])
                    if quantity < 0:
                        planned = None
                        break
                    if at < instant:
                        overdue += 1
                    elif at < until:
                        planned += quantity
        reference = max(0, daily * days)
        result.append(
            {
                "item_id": sku,
                "available": available,
                "planned_inbound": planned,
                "reference_demand": reference,
                "daily_average": daily,
                "cover_days": available / daily if available is not None and daily > 0 else None,
                "reference_shortfall": max(0, reference - available - planned)
                if available is not None and planned is not None
                else None,
                "overdue_plans_excluded": overdue,
                "unit": config["unit"],
            }
        )
    return {
        "rows": sorted(result, key=lambda r: (r["cover_days"] is None, r["cover_days"] or 0)),
        "planning_days": days,
        "start": today.isoformat(),
        "end": (today + timedelta(days=days - 1)).isoformat(),
        "evidence": {
            "formulas": {
                "reference_demand": "mean(last 14 observed calendar days) × planning_days",
                "cover_days": "sum(authorized available stock across warehouses) / daily average",
                "planned_inbound": "approved remaining quantity with future ETA inside the horizon",
            },
            "join": "same organization, SKU and UOM; stock summed across authorized warehouses",
            "limitations": [
                "Reference demand is not a model forecast or a probability",
                "Does not include unallocated backlog; use the scenario planner for deadlines",
                "Plans are not received goods; overdue plans excluded",
                "Source reads are not a cross-database transaction snapshot",
            ],
            "as_of": instant.isoformat(),
        },
    }


def carrier_summary(rows, days, instant, timezone="Asia/Ho_Chi_Minh"):
    today = instant.astimezone(ZoneInfo(timezone)).date()
    groups = defaultdict(list)
    for row in rows:
        day = local_day(row["capacity_date"], timezone)
        if today <= day < today + timedelta(days=days):
            groups[row["carrier_code"]].append(row)
    result = []
    for carrier, calendar in sorted(groups.items()):
        capacities = [
            numeric(r["max_dispatches"]) if r["status"] == "ACTIVE" else 0 for r in calendar
        ]
        used = [numeric(r["used_dispatches"]) for r in calendar]
        result.append(
            {
                "carrier": carrier,
                "booked_slots": sum(used),
                "remaining_slots": sum(
                    max(0, c - u) for c, u in zip(capacities, used, strict=True)
                ),
                "published_days": len({str(r["capacity_date"]) for r in calendar}),
                "overbooked_days": sum(u > c for c, u in zip(capacities, used, strict=True)),
            }
        )
    return {
        "rows": result,
        "planning_days": days,
        "start": today.isoformat(),
        "end": (today + timedelta(days=days - 1)).isoformat(),
        "evidence": {
            "unit": "dispatches",
            "as_of": instant.isoformat(),
            "formula": "sum per day max(0, ACTIVE capacity - booked dispatches)",
            "limitations": [
                "Known bookings only; no SKU quantity-to-dispatch conversion",
                "Unpublished calendar days are unknown, not zero capacity",
            ],
        },
    }
