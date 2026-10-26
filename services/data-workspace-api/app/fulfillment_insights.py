# SPDX-License-Identifier: GPL-3.0-or-later
"""Conditional order promising over one authorized snapshot, with joint SKU demand.

This is a planning policy simulation, not a trained order-outcome classifier.
All outstanding orders are considered at snapshot time, earliest deadline first.
Whole orders reserve supply when every line is available; blocked orders may be
skipped. Future forecast demand consumes remaining free stock at each day end.
No booking, reservation or source data is changed by this calculation.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .analytics import numeric
from .planning import moment

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
EPSILON = 1e-7


def pickup_check(estimate, deadline, schedules, capacity):
    """Do not turn an indicative preparation date into a promised carrier handoff."""
    if estimate["state"] in {"late", "beyond_window"}:
        return {"state": "blocked", "reason": "Theo giả định, Kho chưa chuẩn bị kịp hạn bàn giao."}
    if estimate["state"] == "unknown":
        return {"state": "unknown", "reason": "Chưa đủ dữ liệu để xác nhận mốc chuẩn bị."}
    if len(schedules) != 1 or not schedules[0].get("scheduled_handoff_at"):
        return {
            "state": "unknown",
            "reason": "Cần một lịch lấy hàng đã xác nhận cho toàn đơn; "
            "nhiều lịch cần đối chiếu lượng hàng từng đợt.",
        }
    shipment = schedules[0]
    at = moment(shipment["scheduled_handoff_at"])
    if at < moment(estimate["preparation_complete_at"]) or at > deadline:
        return {
            "state": "blocked",
            "reason": "Giờ xe lấy hàng nằm trước mốc chuẩn bị xong hoặc sau hạn bàn giao.",
        }
    entries = [
        r
        for r in capacity or []
        if r["carrier_code"] == shipment["carrier_code"]
        and str(r["capacity_date"])[:10] == at.astimezone(ZONE).date().isoformat()
    ]
    if len(entries) != 1:
        return {
            "state": "unknown",
            "reason": "Chưa có một bản ghi số lượt nhận hợp lệ cho lịch này.",
        }
    entry = entries[0]
    used, maximum = numeric(entry["used_dispatches"]), numeric(entry["max_dispatches"])
    if entry["status"] != "ACTIVE" or maximum <= 0 or used > maximum:
        return {
            "state": "blocked",
            "reason": "Lịch nhà vận chuyển ngừng nhận hoặc đã vượt số lượt.",
        }
    if used < 1:
        return {
            "state": "unknown",
            "reason": "Chưa đối chiếu được lịch đã nhận với số lượt đã dùng.",
        }
    return {
        "state": "conditional",
        "reason": "Mốc chuẩn bị, lịch lấy hàng và số lượt công bố "
        "phù hợp trong dữ liệu hiện tại; chưa xác nhận bàn giao thực tế.",
    }


def project_joint(
    stocks,
    inbounds,
    known,
    forecasts,
    start,
    horizon,
    snapshot,
    *,
    quantile="p50",
    delay_days=0,
    plan_id=None,
):
    """Deterministic, whole-order, earliest-deadline-first material assignment."""
    balances = {(r["sku"], r["uom"]): numeric(r["available_quantity"]) for r in stocks}
    end = datetime.combine(start + timedelta(days=horizon), time.min, ZONE)
    orders = sorted(
        (o for o in known if o.get("handoff_deadline", o["deadline"]) < end),
        key=lambda o: (o["deadline"], o["id"]),
    )
    missing = sorted({key for o in orders for key in o["lines"] if key not in balances})
    if missing:
        return {"available": False, "missing_stock": [dict(sku=k[0], uom=k[1]) for k in missing]}
    pending = list(orders)
    assigned, attribution = {}, {}
    lots = {key: [["stock", value]] for key, value in balances.items()}
    demand_backlog = defaultdict(float)
    sources = {}
    events = defaultdict(list)
    events[snapshot]  # Assign initial stock before any future forecast events.
    for row in inbounds:
        at = moment(row["expected_at"])
        if row["status"] not in {"APPROVED", "PARTIALLY_RECEIVED"} or at < snapshot:
            continue
        identifier = str(row["inbound_plan_id"])
        if plan_id is None or identifier == plan_id:
            at += timedelta(days=delay_days)
        if at >= end:
            continue
        key = (row["sku"], row["uom"])
        sources[identifier] = {
            "plan_id": identifier,
            "sku": key[0],
            "uom": key[1],
            "expected_at": at.isoformat(),
        }
        events[at].append((key, numeric(row["remaining_quantity"]), identifier))
    for sku, series in forecasts.items():
        stock = next((r for r in stocks if r["sku"] == sku), None)
        if stock is None:
            continue
        for point in series:
            at = datetime.combine(
                datetime.fromisoformat(point["timestamp"]).date() + timedelta(days=1),
                time.min,
                ZONE,
            ) - timedelta(microseconds=1)
            if snapshot < at <= end:
                events[at].append(((sku, stock["uom"]), -numeric(point[quantile]), "forecast"))

    def consume(key, quantity):
        used = []
        while quantity > EPSILON and lots.get(key):
            lot = lots[key][0]
            amount = min(quantity, lot[1])
            if amount > EPSILON:
                used.append((lot[0], amount))
            lot[1] -= amount
            quantity -= amount
            if lot[1] <= EPSILON:
                lots[key].pop(0)
        return used

    short_at_deadline = {}
    # Deadline checkpoints measure deficits without waiting until day end.
    for order in orders:
        events[max(snapshot, order["deadline"])]
    for at, incoming in sorted(events.items()):
        if at > end:
            continue
        for key, quantity, identifier in incoming:
            if identifier == "forecast":
                demand_backlog[key] -= quantity
            else:
                balances[key] = balances.get(key, 0) + quantity
                lots.setdefault(key, []).append([identifier, quantity])
        for order in pending[:]:
            if all(balances[key] + EPSILON >= q for key, q in order["lines"].items()):
                assigned[order["id"]] = at
                attribution[order["id"]] = []
                for key, quantity in order["lines"].items():
                    balances[key] -= quantity
                    attribution[order["id"]].extend(
                        {"source_id": source, "sku": key[0], "uom": key[1], "quantity": amount}
                        for source, amount in consume(key, quantity)
                    )
                pending.remove(order)
        for order in pending:
            if order["deadline"] <= at and order["id"] not in short_at_deadline:
                short_at_deadline[order["id"]] = [
                    {"sku": key[0], "uom": key[1], "missing_quantity": max(0, q - balances[key])}
                    for key, q in order["lines"].items()
                    if q > balances[key] + EPSILON
                ]
        for key, quantity in demand_backlog.items():
            used = min(quantity, balances.get(key, 0))
            consume(key, used)
            balances[key] = balances.get(key, 0) - used
            demand_backlog[key] -= used
    return {
        "available": True,
        "assigned": assigned,
        "shortages": short_at_deadline,
        "attribution": attribution,
        "sources": sources,
    }


def fulfillment_insights(
    *,
    stocks,
    inbounds,
    known,
    forecasts,
    headers,
    policy,
    start,
    horizon,
    snapshot,
    products,
    schedules,
    capacity,
    forecast_method,
    delay_days=2,
    plan_id=None,
):
    if len(headers) > 1000 or len(inbounds) > 1000:
        raise HTTPException(
            422,
            {
                "code": "INSIGHTS_TOO_LARGE",
                "message": "Thu hẹp kỳ kiểm tra: tối đa 1.000 đơn và 1.000 đợt hàng.",
            },
        )
    end = datetime.combine(start + timedelta(days=horizon), time.min, ZONE)
    selected = [r for r in inbounds if plan_id is None or str(r["inbound_plan_id"]) == plan_id]
    if plan_id and not selected:
        raise HTTPException(
            422,
            {
                "code": "INSIGHT_PLAN_NOT_FOUND",
                "message": "Đợt hàng không thuộc kho hoặc không còn trong kế hoạch.",
            },
        )
    if plan_id and (
        moment(selected[0]["expected_at"]) < snapshot or moment(selected[0]["expected_at"]) >= end
    ):
        raise HTTPException(
            422,
            {
                "code": "INSIGHT_PLAN_OUTSIDE_WINDOW",
                "message": "Chọn đợt hàng chưa quá hạn và nằm trong kỳ kiểm tra.",
            },
        )
    arguments = (stocks, inbounds, known, forecasts, start, horizon, snapshot)
    base = project_joint(*arguments)
    cautious = project_joint(*arguments, quantile="p90")
    delayed = project_joint(*arguments, delay_days=delay_days, plan_id=plan_id)
    if not base["available"]:
        return {
            "available": False,
            "missing_stock": base["missing_stock"],
            "orders": [],
            "message": "Thiếu bản ghi tồn cho mặt hàng của đơn; chưa thể phân bổ chung toàn kho.",
        }
    remaining = {o["id"]: o for o in known}
    labels = {(p["sku"], p["uom"]): p.get("name") for p in products}
    rows, bottlenecks = [], {}

    def estimate(identifier, projection, deadline):
        order = remaining.get(identifier)
        at = projection["assigned"].get(identifier) if order else snapshot
        ready = at + timedelta(minutes=policy["minutes"]) if at and policy else None
        state = (
            "unknown"
            if policy is None
            else "beyond_window"
            if at is None
            else ("late" if ready > deadline else "on_time")
        )
        return {
            "material_ready_at": at.isoformat() if at else None,
            "preparation_complete_at": ready.isoformat() if ready else None,
            "state": state,
            "slack_hours": (deadline - ready).total_seconds() / 3600 if ready else None,
        }

    for identifier, header in sorted(
        headers.items(), key=lambda x: (x[1]["requested_handoff_at"], x[0])
    ):
        deadline = moment(header["requested_handoff_at"])
        if deadline >= end:
            continue
        estimates = {
            "base": estimate(identifier, base, deadline),
            "high_demand": estimate(identifier, cautious, deadline),
            "delayed": estimate(identifier, delayed, deadline),
        }
        baseline = estimates["base"]
        latest = estimates["delayed"]
        worsened = bool(
            baseline["preparation_complete_at"]
            and (
                not latest["preparation_complete_at"]
                or moment(latest["preparation_complete_at"])
                > moment(baseline["preparation_complete_at"])
            )
        )
        order_known = remaining.get(identifier)
        absent_forecast = [
            k[0] for k in (order_known or {}).get("lines", {}) if k[0] not in forecasts
        ]
        if absent_forecast:
            for estimate_value in estimates.values():
                if estimate_value["state"] == "on_time":
                    estimate_value["state"] = "unknown"
        row = {
            "order_id": identifier,
            "order_number": header["order_number"],
            "deadline": deadline.isoformat(),
            "already_overdue": deadline < snapshot,
            **estimates,
            "affected_by_delay": worsened,
            "newly_late": baseline["state"] == "on_time"
            and latest["state"] in {"late", "beyond_window"},
            "missing_forecast_skus": absent_forecast,
            "shortages": base["shortages"].get(identifier, []),
            "supply_links": base["attribution"].get(identifier, []),
            "lines": [
                {"sku": k[0], "uom": k[1], "quantity": q, "name": labels.get(k)}
                for k, q in (order_known or {}).get("lines", {}).items()
            ],
            "schedules": [
                dict(r)
                for r in schedules or []
                if str(r["order_id"]) == identifier and r["status"] == "ACCEPTED"
            ],
            "all_lines_held_or_dispatched": order_known is None,
        }
        row["pickup_checks"] = {
            key: pickup_check(value, deadline, row["schedules"], capacity)
            for key, value in estimates.items()
        }
        rows.append(row)
        for shortage in row["shortages"]:
            key = (shortage["sku"], shortage["uom"])
            item = bottlenecks.setdefault(
                key, {"sku": key[0], "uom": key[1], "name": labels.get(key), "order_ids": []}
            )
            item["order_ids"].append(identifier)
    ranking = sorted(
        ({**v, "order_count": len(set(v["order_ids"]))} for v in bottlenecks.values()),
        key=lambda r: (-r["order_count"], r["sku"], r["uom"]),
    )
    total = sum(r["order_count"] for r in ranking)
    cumulative = 0
    for item in ranking:
        cumulative += item["order_count"]
        item["cumulative_percent"] = 100 * cumulative / total if total else None
    heatmap = []
    for entry in capacity or []:
        day = str(entry["capacity_date"])[:10]
        if str(start) <= day < str(start + timedelta(days=horizon)):
            maximum = numeric(entry["max_dispatches"]) if entry["status"] == "ACTIVE" else 0
            used = numeric(entry["used_dispatches"])
            heatmap.append(
                {
                    "day": day,
                    "carrier_code": entry["carrier_code"],
                    "maximum": maximum,
                    "used": used,
                    "remaining": max(0, maximum - used),
                    "utilization_percent": used * 100 / maximum if maximum else None,
                }
            )
    return {
        "available": True,
        "as_of": snapshot.isoformat(),
        "start": str(start),
        "end": str(start + timedelta(days=horizon - 1)),
        "forecast_method": forecast_method,
        "delay_days": delay_days,
        "delayed_plan_id": plan_id,
        "preparation_minutes": policy["minutes"] if policy else None,
        "orders": rows,
        "bottlenecks": ranking,
        "capacity": sorted(heatmap, key=lambda r: (r["carrier_code"], r["day"])),
        "capacity_available": capacity is not None,
        "inbound_options": [
            {
                "plan_id": str(r["inbound_plan_id"]),
                "sku": r["sku"],
                "uom": r["uom"],
                "quantity": numeric(r["remaining_quantity"]),
                "expected_at": str(r["expected_at"]),
            }
            for r in inbounds
            if snapshot <= moment(r["expected_at"]) < end
        ],
        "summary": {
            "total_orders": len(rows),
            "on_time": sum(r["base"]["state"] == "on_time" for r in rows),
            "newly_late": sum(r["newly_late"] for r in rows),
            "affected": sum(r["affected_by_delay"] for r in rows),
            "high_demand_exposed": sum(
                r["base"]["state"] == "on_time"
                and r["high_demand"]["state"] in {"late", "beyond_window"}
                for r in rows
            ),
        },
        "limitations": [
            "Phân bổ giả lập toàn đơn theo hạn sớm trước; đơn thiếu món có thể được bỏ qua. "
            "Đây chưa phải việc giữ hàng thực tế hoặc lịch sản xuất của Kho.",
            "Hàng đã giữ/đã bàn giao không trừ lại. Dự báo mới dùng hàng còn lại vào cuối ngày.",
            "Mốc dự kiến là khi đủ hàng cộng thời gian chuẩn bị. Chưa tự xác nhận lịch xe, "
            "không dự đoán giao đến khách hoặc xác suất trễ.",
            "Thời gian chuẩn bị áp dụng cho từng đơn; chưa lập lịch nhân sự hoặc giới hạn "
            "số đơn Kho có thể soạn đồng thời.",
            "P90 từng ngày là kịch bản nhu cầu cao; không phải xác suất hoàn tất 90% của cả đơn.",
            "Mặt hàng chặn nhiều đơn đếm liên hệ đơn–mặt hàng; một đơn có thể có nhiều món thiếu. "
            "Không cộng số lượng thiếu lặp lại thành lượng cần mua.",
            "Nguồn đọc tại các thời điểm gần nhau. Phải xác minh lại trước khi thực hiện.",
        ],
    }
