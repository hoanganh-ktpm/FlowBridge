# SPDX-License-Identifier: GPL-3.0-or-later
"""Decision workspace for a merchandise planner; only governed read interfaces."""

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from time import monotonic
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .analytics import local_day, numeric, prepare
from .forecasting import statistical_forecast
from .learning import validated_forecast
from .planning import known_orders, moment, project_orders
from .schemas import AnalysisConfig

logger = logging.getLogger(__name__)


async def planning_report(body, ctx, connector, repository, source_names=None):
    now = datetime.now(UTC)
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    today = now.astimezone(zone).date()
    end = today + timedelta(days=body.horizon - 1)
    facts, sources, missing = {}, [], []
    definitions = {
        "demand": (
            "sales.planning_demand_daily",
            ["day", "sku", "uom", "warehouse_id", "confirmed_quantity"],
        ),
        "stock": (
            "warehouse.stock_availability",
            [
                "warehouse_id",
                "sku",
                "uom",
                "on_hand_quantity",
                "reserved_quantity",
                "available_quantity",
                "updated_at",
            ],
        ),
        "inbound": (
            "warehouse.inbound_schedule",
            [
                "inbound_plan_id",
                "warehouse_id",
                "sku",
                "uom",
                "expected_at",
                "status",
                "remaining_quantity",
            ],
        ),
        "open_orders": (
            "sales.open_orders",
            [
                "order_id",
                "order_number",
                "status",
                "warehouse_id",
                "order_line_id",
                "sku",
                "uom",
                "ordered_quantity",
                "requested_handoff_at",
                "confirmed_at",
            ],
        ),
        "commitments": (
            "warehouse.order_commitments",
            [
                "order_id",
                "order_line_id",
                "warehouse_id",
                "sku",
                "uom",
                "dispatched_quantity",
                "held_quantity",
            ],
        ),
        "catalog": (
            "warehouse.planning_catalog",
            [
                "warehouse_id",
                "product_id",
                "sku",
                "product_name",
                "uom",
                "product_status",
                "warehouse_code",
                "warehouse_name",
            ],
        ),
        "receipts": (
            "warehouse.receipts_daily",
            ["warehouse_id", "sku", "uom", "day", "received_quantity"],
        ),
        "schedule": (
            "logistics.handoff_schedule",
            [
                "shipment_id",
                "shipment_number",
                "order_id",
                "order_number",
                "warehouse_id",
                "carrier_code",
                "carrier_name",
                "planned_handoff_at",
                "scheduled_handoff_at",
                "accepted_at",
                "handed_over_at",
                "status",
            ],
        ),
        "capacity": (
            "logistics.handoff_capacity",
            ["carrier_code", "capacity_date", "max_dispatches", "used_dispatches", "status"],
        ),
    }

    async def read(key, where=None):
        started = monotonic()
        reference = getattr(body, key)
        if reference is None:
            facts[key] = None
            missing.append(key)
            return
        expected, columns = definitions[key]
        expected = (source_names or {}).get(key, expected)
        rows = []
        for selection in where if isinstance(where, list) else [where]:
            product, batch = await connector.rows(ctx, reference, columns, where=selection)
            if product["name"] != expected:
                raise HTTPException(
                    422, "Published source does not match the requested planning role"
                )
            rows.extend(batch)
        logger.info("Planning source %s: %d rows in %.2fs", key, len(rows), monotonic() - started)
        facts[key] = rows
        sources.append(
            {"ref": reference.model_dump(mode="json"), "columns": columns, "name": product["name"]}
        )

    # Resolve the warehouse from the small stock source before paginating history.
    # Filtering after downloading every warehouse can exceed Core's page budget.
    await read("stock")
    warehouses = sorted({str(r["warehouse_id"]) for r in facts["stock"]})
    if not warehouses:
        raise HTTPException(
            422, detail={"code": "NO_DATA", "message": "Chưa có số liệu tồn kho để lập kế hoạch."}
        )
    wh = str(body.warehouse_id) if body.warehouse_id else warehouses[0]
    if wh not in warehouses:
        raise HTTPException(422, "Warehouse is outside the authorized stock scope")
    # Allow 180 training days even when the latest observation lags by 90 days.
    # Also cover the requested chart period; filtering must never trim its totals.
    history_floor = min(
        today - timedelta(days=body.training_days + 89),
        body.history_start or today - timedelta(days=body.history_days + 89),
    )

    async def read_scoped(key):
        where = {} if key == "capacity" else {"warehouse_id": {"_eq": wh}}
        if key in {"demand", "receipts"}:
            where["day"] = {"_gte": history_floor.isoformat(), "_lt": today.isoformat()}
        if key == "capacity":
            where["capacity_date"] = {"_gte": today.isoformat(), "_lte": end.isoformat()}
        if key in {"commitments", "schedule"}:
            # Historical reservations/shipments can be much larger than demand.
            # Only current open orders participate in this report. Keep each _in
            # predicate within Core's 100-value contract, with disjoint batches.
            await open_orders_task
            order_ids = sorted({str(r["order_id"]) for r in facts["open_orders"]})
            selections = [
                {**where, "order_id": {"_in": order_ids[i : i + 100]}}
                for i in range(0, len(order_ids), 100)
            ]
            await read(key, selections or {**where, "order_id": {"_is_null": True}})
            return
        await read(key, where)

    open_orders_task = asyncio.create_task(read("open_orders", {"warehouse_id": {"_eq": wh}}))
    tasks = [
        open_orders_task,
        *(
            asyncio.create_task(read_scoped(k))
            for k in definitions
            if k not in {"stock", "open_orders"}
        ),
    ]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    stock = [r for r in facts["stock"] if str(r["warehouse_id"]) == wh]
    inbound = [
        r
        for r in facts["inbound"]
        if str(r["warehouse_id"]) == wh and r["status"] in {"APPROVED", "PARTIALLY_RECEIVED"}
    ]
    catalog = [r for r in (facts["catalog"] or []) if str(r["warehouse_id"]) == wh]
    labels = {r["sku"]: r for r in catalog}
    all_skus = sorted({r["sku"] for r in stock})
    if len(all_skus) > 20:
        raise HTTPException(422, "Planning supports up to 20 products per authorized warehouse")
    selected = body.sku or all_skus[0]
    if selected not in all_skus:
        raise HTTPException(422, "Select a product in the current warehouse")
    history = [
        r
        for r in facts["demand"]
        if str(r["warehouse_id"]) == wh and local_day(r["day"], "Asia/Ho_Chi_Minh") < today
    ]
    if not history:
        raise HTTPException(
            422,
            detail={"code": "NO_DATA", "message": "Chưa có lịch sử đặt hàng để ước tính nhu cầu."},
        )
    last = max(local_day(r["day"], "Asia/Ho_Chi_Minh") for r in history)
    first = min(local_day(r["day"], "Asia/Ho_Chi_Minh") for r in history)
    if not 1 <= (today - last).days <= 90:
        raise HTTPException(
            422,
            detail={
                "code": "STALE_HISTORY",
                "message": "Lịch sử đặt hàng quá cũ. Cần cập nhật nguồn trước khi lập kế hoạch.",
            },
        )
    unit_by_sku = {}
    for r in stock:
        if r["sku"] in unit_by_sku:
            raise HTTPException(422, "Duplicate warehouse/SKU stock grain")
        unit_by_sku[r["sku"]] = r["uom"]
    if any(r["sku"] in unit_by_sku and r["uom"] != unit_by_sku[r["sku"]] for r in history):
        raise HTTPException(422, "Demand and inventory UOM mismatch")
    eligible = sorted(set(all_skus) & {r["sku"] for r in history})
    if len({unit_by_sku[s] for s in eligible}) > 1:
        # Forecast each unit independently; never add distinct UOM quantities.
        groups = defaultdict(list)
        for sku in eligible:
            groups[unit_by_sku[sku]].append(sku)
    else:
        groups = {unit_by_sku[eligible[0]]: eligible} if eligible else {}
    runs, points = {}, {}
    jobs = []
    for unit, skus in groups.items():
        if body.forecast_model == "ridge_lag" and selected in skus:
            jobs.append((unit, [selected], "ridge_lag"))
            if others := [sku for sku in skus if sku != selected]:
                jobs.append((unit, others, "seasonal_mean"))
        else:
            jobs.append((unit, skus, "seasonal_mean"))
    for unit, skus, method in jobs:
        config = AnalysisConfig(
            name="Kế hoạch hàng hóa",
            **body.demand.model_dump(),
            time_column="day",
            entity_column="sku",
            target_column="confirmed_quantity",
            unit=unit,
            unit_column="uom",
            filters={"warehouse_id": wh},
            aggregation="SUM",
            frequency="D",
            missing_policy="zero",
            start=max(first, last - timedelta(days=body.training_days - 1)),
            end=last,
            entities=skus,
            forecast_start=today,
            forecast_horizon=body.horizon,
            forecast_model=method,
        )
        product = {
            "name": (source_names or {}).get("demand", "sales.planning_demand_daily"),
            "fields": [
                {"name": n} for n in ["day", "sku", "uom", "warehouse_id", "confirmed_quantity"]
            ],
            "business_key": ["org_id", "sku", "day"],
        }
        dataset = prepare(
            config,
            [r for r in history if r["uom"] == unit],
            product,
            known_event_entities={r["sku"] for r in history if r["uom"] == unit},
        )
        forecast, model = await asyncio.to_thread(
            validated_forecast if method == "ridge_lag" else statistical_forecast, dataset, config
        )
        payload = {
            "status": "SUCCEEDED",
            "config": config.model_dump(mode="json"),
            "points": forecast,
            "model": model,
            "evidence": {
                **dataset["evidence"],
                "model": model,
                "requested_at": now.isoformat(),
                "origin_policy": "request_date",
                "purpose": "merchandise_plan",
            },
            "sources": [
                {
                    "ref": body.demand.model_dump(mode="json"),
                    "columns": ["day", "sku", "uom", "warehouse_id", "confirmed_quantity"],
                }
            ],
            "quality": dataset["quality"],
            "metrics": [],
            "created_at": now.isoformat(),
        }
        # Keep persisted runs inside current permission boundary.
        await connector.check(
            ctx, body.demand, ["day", "sku", "uom", "warehouse_id", "confirmed_quantity"]
        )
        run = await repository.save("forecast", ctx, payload)
        for sku in skus:
            runs[sku] = run
            points[sku] = [p for p in forecast if p["item_id"] == sku]
    known = known_orders(facts["open_orders"], facts["commitments"], wh, "Asia/Ho_Chi_Minh")
    plans, baselines = {}, {}
    for sku in all_skus:
        unit = unit_by_sku[sku]
        s = next(r for r in stock if r["sku"] == sku)
        opening = numeric(s["on_hand_quantity"]) - numeric(s["reserved_quantity"])
        if opening < 0:
            raise HTTPException(422, "Stock is inconsistent with reservations")
        future = points.get(sku)
        baseline = (
            project_orders(future, stock, inbound, known, sku, unit, today, snapshot_at=now)
            if future
            else None
        )
        # A second projection changes only new forecast demand to zero. This
        # separates existing commitments from additional forecast exposure,
        # including supply arriving after a deadline on the same calendar day.
        zero_points = [
            {"timestamp": (today + timedelta(days=i)).isoformat(), "p50": 0}
            for i in range(body.horizon)
        ]
        known_only = project_orders(
            zero_points, stock, inbound, known, sku, unit, today, snapshot_at=now
        )
        known_shortage = known_only["metrics"]["peak_supply_shortage"]
        combined_shortage = baseline["metrics"]["peak_supply_shortage"] if baseline else None
        baselines[sku] = baseline
        coming = [
            r
            for r in inbound
            if r["sku"] == sku
            and r["uom"] == unit
            and now <= moment(r["expected_at"])
            and local_day(r["expected_at"], "Asia/Ho_Chi_Minh") <= end
        ]
        overdue = [r for r in inbound if r["sku"] == sku and moment(r["expected_at"]) < now]
        q = sum(numeric(r["remaining_quantity"]) for r in coming)
        b = baseline["metrics"] if baseline else None
        risk_orders = (
            [
                o
                for o in baseline["orders"]
                if o["at_risk"] and any(line["sku"] == sku for line in o["shortages_at_deadline"])
            ]
            if baseline
            else []
        )
        first_risk = (
            next(
                (
                    p["day"]
                    for p in baseline["series"]
                    if p["supply_shortage"] > 1e-9
                    or any(
                        o["at_risk"]
                        and local_day(o["deadline"], "Asia/Ho_Chi_Minh")
                        <= local_day(p["day"], "Asia/Ho_Chi_Minh")
                        and any(line["sku"] == sku for line in o["shortages_at_deadline"])
                        for o in baseline["orders"]
                    )
                ),
                None,
            )
            if baseline
            else None
        )
        shortage = combined_shortage
        next_eta = min((r["expected_at"] for r in coming), default=None)
        attention = [
            r
            for r in coming
            if first_risk
            and local_day(r["expected_at"], "Asia/Ho_Chi_Minh")
            > local_day(first_risk, "Asia/Ho_Chi_Minh")
        ]
        plans[sku] = {
            "sku": sku,
            "name": labels.get(sku, {}).get("product_name"),
            "uom": unit,
            "on_hand": numeric(s["on_hand_quantity"]),
            "reserved": numeric(s["reserved_quantity"]),
            "available": opening,
            "forecast_quantity": b["forecast_demand"] if b else None,
            "known_quantity": b["known_unallocated_demand"] if b else None,
            "incoming_quantity": q,
            "shortage": shortage,
            "known_order_shortage": known_shortage,
            "forecast_added_shortage": max(0, combined_shortage - known_shortage)
            if combined_shortage is not None
            else None,
            "first_risk_date": first_risk,
            "orders_at_risk": len(risk_orders),
            "next_arrival": next_eta,
            "priority": "unknown"
            if not baseline
            else "urgent"
            if (shortage or 0) > 1e-9 or risk_orders
            else "watch"
            if overdue
            else "clear",
            "late_plan_ids": [str(r["inbound_plan_id"]) for r in [*overdue, *attention]],
            "forecast_id": runs[sku]["id"] if sku in runs else None,
        }
    if not baselines.get(selected):
        raise HTTPException(
            422,
            detail={"code": "NO_DATA", "message": "Sản phẩm này chưa đủ lịch sử để lập kế hoạch."},
        )
    chosen = baselines[selected]
    orders_by_id = {}
    for baseline in baselines.values():
        if not baseline:
            continue
        for o in baseline["orders"]:
            if o["order_id"] not in orders_by_id or o["at_risk"]:
                orders_by_id[o["order_id"]] = dict(o)
    schedules = [r for r in (facts["schedule"] or []) if str(r["warehouse_id"]) == wh]
    capacity = facts["capacity"]
    order_headers = {
        str(r["order_id"]): r for r in facts["open_orders"] if str(r["warehouse_id"]) == wh
    }
    for oid, h in order_headers.items():
        if moment(h["requested_handoff_at"]) >= datetime.combine(
            end + timedelta(days=1), datetime.min.time(), zone
        ):
            continue
        if oid not in orders_by_id:
            orders_by_id[oid] = {
                "order_id": oid,
                "order_number": h["order_number"],
                "deadline": h["requested_handoff_at"],
                "at_risk": False,
                "lines": [],
                "shortages_at_deadline": [],
                "already_overdue": moment(h["requested_handoff_at"]) < now,
                "fulfilled_at": None,
            }
        o = orders_by_id[oid]
        matching = [r for r in schedules if str(r["order_id"]) == oid and r["status"] == "ACCEPTED"]
        late = [
            r
            for r in matching
            if r["scheduled_handoff_at"]
            and moment(r["scheduled_handoff_at"]) > moment(o["deadline"])
        ]
        o["schedule_status"] = (
            "unknown"
            if facts["schedule"] is None
            else "late"
            if late
            else "booked"
            if matching
            else "unbooked"
        )
        o["shipments"] = [
            {
                k: r[k]
                for k in [
                    "shipment_number",
                    "carrier_code",
                    "carrier_name",
                    "scheduled_handoff_at",
                    "status",
                ]
            }
            for r in matching
        ]
        o["products"] = [
            {"sku": r["sku"], "name": labels.get(r["sku"], {}).get("product_name"), "uom": r["uom"]}
            for r in facts["open_orders"]
            if str(r["order_id"]) == oid
        ]
        o["reason"] = (
            "stock"
            if o["at_risk"]
            else "schedule_unknown"
            if facts["schedule"] is None
            else "schedule_late"
            if late
            else "schedule_unconfirmed"
            if not matching
            else "scheduled"
        )
    inbound_details = [
        {
            "plan_id": str(r["inbound_plan_id"]),
            "sku": r["sku"],
            "name": labels.get(r["sku"], {}).get("product_name"),
            "quantity": numeric(r["remaining_quantity"]),
            "uom": r["uom"],
            "expected_at": r["expected_at"],
            "overdue": moment(r["expected_at"]) < now,
            "status": r["status"],
        }
        for r in inbound
    ]
    history_end = min(body.history_end, last) if body.history_end else last
    history_start = body.history_start or history_end - timedelta(days=body.history_days - 1)
    history_start = max(first, history_start)
    if history_start > history_end:
        raise HTTPException(422, "No historical data in this period")
    demand_days = defaultdict(float)
    for row in history:
        day = local_day(row["day"], "Asia/Ho_Chi_Minh")
        if row["sku"] == selected and history_start <= day <= history_end:
            demand_days[day] += numeric(row["confirmed_quantity"])
    receipts_days = defaultdict(float)
    if facts["receipts"] is not None:
        for row in facts["receipts"]:
            if (
                str(row["warehouse_id"]) == wh
                and row["sku"] == selected
                and row["uom"] != unit_by_sku[selected]
            ):
                raise HTTPException(422, "Receipts and demand UOM mismatch")
            day = local_day(row["day"], "Asia/Ho_Chi_Minh")
            if (
                str(row["warehouse_id"]) == wh
                and row["sku"] == selected
                and row["uom"] == unit_by_sku[selected]
                and history_start <= day <= history_end
            ):
                receipts_days[day] += numeric(row["received_quantity"])
    history_series = []
    day = history_start
    while day <= history_end:
        receipt = receipts_days[day] if facts["receipts"] is not None else None
        history_series.append(
            {
                "day": day.isoformat(),
                "confirmed": demand_days[day],
                "received": receipt,
                "difference": receipt - demand_days[day] if receipt is not None else None,
            }
        )
        day += timedelta(days=1)
    calendar = []
    for row in capacity or []:
        day = local_day(row["capacity_date"], "Asia/Ho_Chi_Minh")
        if today <= day <= end:
            maximum = numeric(row["max_dispatches"]) if row["status"] == "ACTIVE" else 0
            used = numeric(row["used_dispatches"])
            name = next(
                (r["carrier_name"] for r in schedules if r["carrier_code"] == row["carrier_code"]),
                None,
            )
            calendar.append(
                {
                    "day": day.isoformat(),
                    "carrier_code": row["carrier_code"],
                    "carrier_name": name,
                    "maximum": maximum,
                    "booked": used,
                    "remaining": max(0, maximum - used),
                }
            )
    risk_ids = {
        o["order_id"]
        for o in orders_by_id.values()
        if o["at_risk"] or o["schedule_status"] == "late"
    }
    rows_sorted = sorted(
        plans.values(),
        key=lambda r: (
            {"urgent": 0, "watch": 1, "unknown": 2, "clear": 3}[r["priority"]],
            r["first_risk_date"] or "9999",
            r["sku"],
        ),
    )

    def explain(title, meaning, source_keys, items, formula, notes=None):
        return {
            "title": title,
            "meaning": meaning,
            "applications": [
                {
                    "name": {
                        "sales": "Bán hàng",
                        "warehouse": "Kho hàng",
                        "logistics": "Vận chuyển",
                    }[definitions[k][0].split(".")[0]],
                    "description": {
                        "demand": "Lượng đặt hàng đã xác nhận theo kho và ngày",
                        "stock": "Hàng thực tế và phần đã giữ cho đơn",
                        "inbound": "Phần hàng chưa nhận của kế hoạch đã duyệt",
                        "open_orders": "Các dòng đơn đã xác nhận chưa hoàn tất",
                        "commitments": "Hàng đã giữ và đã bàn giao theo dòng đơn",
                        "catalog": "Tên sản phẩm và tên kho",
                        "receipts": "Lượng được chấp nhận trên phiếu đã ghi nhận",
                        "schedule": "Lịch bàn giao đã được tiếp nhận",
                        "capacity": "Số lượt tối đa và lượt đã đặt theo ngày",
                    }[k],
                }
                for k in source_keys
                if facts.get(k) is not None
            ],
            "items": items,
            "formula": formula,
            "notes": notes or [],
            "read_at": now.isoformat(),
            "last_observation": last.isoformat(),
            "start": today.isoformat(),
            "end": end.isoformat(),
        }

    card = plans[selected]
    explainers = {
        "stock": explain(
            "Hàng còn có thể phân bổ",
            "Phần hàng chưa giữ cho đơn khác tại kho đang chọn.",
            ["stock"],
            [
                {"label": "Đang có trong kho", "value": card["on_hand"], "unit": card["uom"]},
                {"label": "Đã giữ cho đơn", "value": card["reserved"], "unit": card["uom"]},
                {"label": "Còn có thể phân bổ", "value": card["available"], "unit": card["uom"]},
            ],
            f"{card['on_hand']:g} − {card['reserved']:g} = {card['available']:g}",
            ["Không trừ hàng đã giữ thêm lần nữa khi tính nhu cầu mới."],
        ),
        "balance": explain(
            "Nguy cơ thiếu hàng",
            "Đối chiếu nguồn hàng theo thời điểm về và hạn từng đơn. "
            "nhu cầu mới được xét cuối mỗi ngày.",
            ["demand", "stock", "inbound", "open_orders", "commitments"],
            [
                {
                    "label": "Lượng đặt mới dự báo",
                    "value": card["forecast_quantity"],
                    "unit": card["uom"],
                },
                {
                    "label": "Đơn đã nhận, chưa phân bổ",
                    "value": card["known_quantity"],
                    "unit": card["uom"],
                },
                {
                    "label": "Hàng dự kiến về trong kỳ",
                    "value": card["incoming_quantity"],
                    "unit": card["uom"],
                },
                {
                    "label": "Thiếu nhiều nhất trong kỳ",
                    "value": card["shortage"],
                    "unit": card["uom"],
                },
            ],
            "Hàng đầu kỳ + hàng về đúng thời điểm − đơn đang chờ − nhu cầu mới dự kiến",
            [
                "Kế hoạch nhập chưa phải hàng đã nhận.",
                "Đơn chưa phân bổ được xét khi đến hạn. "
                "trong ngày ưu tiên đơn đến hạn trước nhu cầu mới dự kiến.",
                "Lượng thiếu là lớn nhất qua từng ngày. Không suy ra chỉ từ tổng cuối kỳ.",
                "Đơn chỉ được đáp ứng khi tất cả các dòng đều đủ hàng. "
                "hàng về sau hạn không xóa rủi ro đã trễ.",
            ],
        ),
        "history": explain(
            "Đặt hàng và nhập kho",
            "So sánh hai luồng phát sinh của cùng sản phẩm và kho.",
            ["demand", "receipts"],
            [
                {
                    "label": "Đặt hàng xác nhận",
                    "value": sum(r["confirmed"] for r in history_series),
                    "unit": card["uom"],
                },
                {
                    "label": "Nhập kho đã ghi nhận",
                    "value": sum(r["received"] for r in history_series)
                    if facts["receipts"] is not None
                    else None,
                    "unit": card["uom"],
                },
            ],
            "Chênh lệch phát sinh = hàng nhập đã ghi nhận − lượng đặt hàng xác nhận",
            [
                "Không phải biến động tồn kho: thời điểm đặt khác thời điểm xuất, "
                "hàng có thể đã giữ hoặc bàn giao.",
                "Bao gồm lượng từng được xác nhận dù đơn có thể bị hủy sau đó.",
            ],
        ),
    }
    explainers["history"]["start"] = history_start.isoformat()
    explainers["history"]["end"] = history_end.isoformat()
    explainers["risk"] = {
        **explainers["balance"],
        "title": "Nguy cơ thiếu hàng theo sản phẩm",
        "meaning": "Trục ngang là sản phẩm. Trục dọc là lượng thiếu cùng đơn vị. "
        "Đây là thiếu hàng trước hạn đáp ứng, không phải hạn sử dụng hay xác suất.",
        "items": [
            {
                "label": "Thiếu cho đơn đã nhận",
                "value": card["known_order_shortage"],
                "unit": card["uom"],
            },
            {
                "label": "Thiếu tăng thêm khi xét dự báo",
                "value": card["forecast_added_shortage"],
                "unit": card["uom"],
            },
        ],
        "formula": "Tính hai lần trên cùng dữ liệu và lịch hàng về: chỉ đơn đã nhận. "
        "sau đó thêm nhu cầu dự báo. Cột dự báo là phần thiếu tăng thêm giữa hai lần tính.",
        "notes": [
            "Phần đơn đã nhận xét cả thời điểm đến hạn trong ngày. Hàng về muộn không bù "
            "cho việc đã thiếu trước hạn.",
            "Mỗi lần lấy mức thiếu lớn nhất trong kỳ và tại hạn đơn. Không cộng các ngày thiếu.",
            "Hàng còn phân bổ = tồn thực tế trừ hàng đã giữ. Đơn chờ đã trừ hàng giữ và bàn giao.",
            (
                "Mặt hàng đang chọn dùng Machine Learning học đúng kho và đơn vị. "
                "các mặt hàng khác vẫn dùng nhịp cùng thứ trong tuần."
                if body.forecast_model == "ridge_lag"
                else "Dự báo trên Tổng quan dùng nhịp cùng thứ trong tuần để lập kế hoạch. "
                "mở Phương án nhập hàng để học Machine Learning theo kho."
            ),
            "Không xếp chung sản phẩm khác đơn vị. Chỉ hiển thị tối đa 8 sản phẩm, "
            "ưu tiên ngày cần xử lý gần nhất.",
        ],
    }
    history_weekly = body.history_frequency == "week" or (
        body.history_frequency == "auto" and body.history_days >= 90 and not body.history_start
    )
    if history_weekly:
        weekly = defaultdict(
            lambda: {
                "confirmed": 0,
                "received": 0 if facts["receipts"] is not None else None,
                "difference": 0 if facts["receipts"] is not None else None,
            }
        )
        for point in history_series:
            at = local_day(point["day"], "Asia/Ho_Chi_Minh")
            key = (at - timedelta(days=at.weekday())).isoformat()
            r = weekly[key]
            r["confirmed"] += point["confirmed"]
            if point["received"] is not None:
                r["received"] += point["received"]
                r["difference"] += point["difference"]
        history_series = [{"day": day, **values} for day, values in sorted(weekly.items())]
    result = {
        "explanations": explainers,
        "as_of": now.isoformat(),
        "source_end": last.isoformat(),
        "source_age_days": (today - last).days,
        "start": today.isoformat(),
        "end": end.isoformat(),
        "horizon": body.horizon,
        "warehouse_id": wh,
        "warehouses": [
            {
                "id": w,
                "name": next(
                    (
                        r["warehouse_name"]
                        for r in facts["catalog"] or []
                        if str(r["warehouse_id"]) == w
                    ),
                    None,
                ),
                "code": next(
                    (
                        r["warehouse_code"]
                        for r in facts["catalog"] or []
                        if str(r["warehouse_id"]) == w
                    ),
                    None,
                ),
            }
            for w in warehouses
        ],
        "products": rows_sorted,
        "selected_sku": selected,
        "baseline": chosen,
        "forecast": runs[selected],
        "orders": sorted(
            orders_by_id.values(),
            key=lambda o: (not (o["at_risk"] or o["schedule_status"] == "late"), o["deadline"]),
        ),
        "inbound": inbound_details,
        "capacity_calendar": sorted(calendar, key=lambda r: (r["day"], r["carrier_code"])),
        "history": {
            "start": history_start.isoformat(),
            "end": history_end.isoformat(),
            "series": history_series,
            "receipts_available": facts["receipts"] is not None,
            "frequency": "week" if history_weekly else "day",
        },
        "kpis": {
            "risk_products": sum(r["priority"] == "urgent" for r in plans.values()),
            "risk_orders": len(risk_ids),
            "needs_planning": sum((r["shortage"] or 0) > 1e-9 for r in plans.values()),
            "plans_attention": len({p for r in plans.values() for p in r["late_plan_ids"]}),
        },
        "missing_sources": missing,
        "evidence": {
            "sources": sources,
            "executed_at": now.isoformat(),
            "last_observation": last.isoformat(),
            "period": {"start": today.isoformat(), "end": end.isoformat()},
            "limitations": [
                "Nhu cầu dự báo là đơn mới sau thời điểm chốt, không thay thế đơn đã xác nhận.",
                "Nhu cầu và nguồn hàng cùng kho, cùng mã và đơn vị. "
                "không chuyển hàng giữa kho trong lần tính.",
                "Số rủi ro đã biết xét đơn còn chưa phân bổ và lịch đã nhận sau hạn. "
                "đơn chưa có lịch không mặc định là trễ.",
                "Nguồn được đọc ở các thời điểm gần nhau, "
                "không phải transaction xuyên ba database.",
                "Lịch sử đặt hàng gồm xác nhận từng phát sinh, "
                "kể cả hủy sau đó. Không phải bán ròng.",
                "Hàng nhập lịch sử là accepted trên phiếu POSTED, "
                "theo ngày nhận. Khác kế hoạch hàng sẽ về.",
            ],
        },
    }
    # Reauthorization is supplied by the HTTP composition before returning any facts.
    return result, sources
