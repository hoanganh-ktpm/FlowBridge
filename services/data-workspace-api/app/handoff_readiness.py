# SPDX-License-Identifier: GPL-3.0-or-later
"""Warehouse-owned preparation times and explicit handoff conditions."""

from datetime import timedelta

from .planning import moment
from .source_integrity import invalid


def preparation_policy(rows, warehouse_id):
    if rows is None:
        return None
    selected = [row for row in rows if str(row["warehouse_id"]) == str(warehouse_id)]
    if len(selected) > 1:
        invalid("PREPARATION_POLICY_AMBIGUOUS", "Kho có nhiều chính sách chuẩn bị cùng hiệu lực.")
    if not selected or selected[0]["preparation_policy_revision"] == 0:
        return None
    row = selected[0]
    minutes = row["preparation_minutes"]
    if (
        type(minutes) is not int
        or not 0 <= minutes <= 10080
        or not str(row.get("preparation_confirmed_by") or "").strip()
        or not row.get("preparation_confirmed_at")
        or type(row["preparation_policy_revision"]) is not int
        or row["preparation_policy_revision"] < 1
    ):
        invalid("PREPARATION_POLICY_INVALID", "Thời gian chuẩn bị chưa có xác nhận hợp lệ từ Kho.")
    moment(row["preparation_confirmed_at"])
    return {
        "minutes": minutes,
        "revision": row["preparation_policy_revision"],
        "confirmed_at": row["preparation_confirmed_at"],
        "confirmed_by": row["preparation_confirmed_by"],
    }


def shift_to_stock_ready_deadline(orders, policy):
    return [
        {
            **order,
            "handoff_deadline": order["deadline"],
            "deadline": order["deadline"] - timedelta(minutes=policy["minutes"])
            if policy
            else order["deadline"],
            "preparation_policy_revision": policy["revision"] if policy else None,
        }
        for order in orders
    ]


def handoff_conditions(order, policy, schedule_status, capacity_status="unknown"):
    supply = "not_met" if order.get("at_risk") else "met"
    schedule = (
        "met"
        if schedule_status in {"scheduled", "on_time", "booked"}
        else "not_met"
        if schedule_status == "late"
        else "unknown"
    )
    states = {
        "supply": supply,
        "preparation": "met" if policy else "unknown",
        "schedule": schedule,
        "capacity": capacity_status,
    }
    status = (
        "not_met"
        if "not_met" in states.values()
        else "unknown"
        if "unknown" in states.values()
        else "conditionally_feasible"
    )
    return {
        "status": status,
        "conditions": states,
        "stock_ready_by": (
            order.get("stock_ready_by")
            or (
                moment(order.get("handoff_deadline", order["deadline"]))
                - timedelta(minutes=policy["minutes"])
            ).isoformat()
        )
        if policy
        else None,
        "preparation_minutes": policy["minutes"] if policy else None,
        "preparation_policy_revision": policy["revision"] if policy else None,
        "actual_handoff_verified": False,
    }
