# SPDX-License-Identifier: GPL-3.0-or-later
"""Order-level planning evidence, never an actual delivery performance metric."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .planning import moment

CONDITIONS = ("supply", "preparation", "schedule", "capacity")


def analyze_orders(headers, plan, products, horizon, snapshot):
    now = moment(plan["as_of"]) if plan else snapshot
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    start = now.astimezone(zone).date()
    end = start + timedelta(days=horizon - 1)
    boundary = datetime.combine(end + timedelta(days=1), datetime.min.time(), zone)
    assessed = {o["order_id"]: o for o in (plan or {}).get("orders", [])}
    supply = (plan or {}).get("order_supply", {})
    labels = {(p["sku"], p["uom"]): p["name"] for p in products}
    orders, outside = [], 0
    for header in headers:
        if moment(header["deadline"]) >= boundary:
            outside += 1
            continue
        identifier = header["order_id"]
        detail = assessed.get(identifier, {})
        check = supply.get(identifier, {})
        readiness = detail.get("readiness") or {}
        states = {key: readiness.get("conditions", {}).get(key, "unknown") for key in CONDITIONS}
        # Supply must cover every SKU in this order. A projection selected for
        # one arbitrary product cannot establish whole-order readiness.
        states["supply"] = {"risk": "not_met", "clear": "met"}.get(check.get("status"), "unknown")
        if states["schedule"] == "met" and any(
            not shipment.get("scheduled_handoff_at") for shipment in detail.get("shipments", [])
        ):
            states["schedule"] = "unknown"
        states = {
            key: value if value in {"met", "not_met", "unknown"} else "unknown"
            for key, value in states.items()
        }
        classification = (
            "blocked"
            if "not_met" in states.values()
            else "unknown"
            if "unknown" in states.values()
            else "ready"
        )
        orders.append(
            {
                **header,
                "at_risk": states["supply"] == "not_met",
                "already_overdue": moment(header["deadline"]) < now,
                "fulfilled_at": None,
                "reason": detail.get("reason", "schedule_unknown"),
                "schedule_status": detail.get("schedule_status", "unknown"),
                "shipments": detail.get("shipments", []),
                "products": [
                    {
                        "sku": line["sku"],
                        "uom": line["uom"],
                        "name": labels.get((line["sku"], line["uom"])),
                    }
                    for line in header["lines"]
                ],
                "shortages_at_deadline": check.get("shortages", []),
                "supply_notes": check.get("notes", []),
                "classification": classification,
                "readiness": {
                    "status": {
                        "blocked": "not_met",
                        "unknown": "unknown",
                        "ready": "conditionally_feasible",
                    }[classification],
                    "conditions": states,
                    "stock_ready_by": check.get("stock_ready_by"),
                    "preparation_minutes": check.get("preparation_minutes"),
                    "preparation_policy_revision": readiness.get("preparation_policy_revision"),
                    "actual_handoff_verified": False,
                },
            }
        )
    return {
        "as_of": now.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "horizon": horizon,
        "outside_horizon_count": outside,
        "orders": sorted(orders, key=lambda o: (moment(o["deadline"]), o["order_number"])),
        "missing_sources": (plan or {}).get("missing_sources", []),
        "assessment_available": plan is not None,
    }
