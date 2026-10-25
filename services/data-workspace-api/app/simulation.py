# SPDX-License-Identifier: GPL-3.0-or-later
"""Supply-demand projections, independent of forecasting and UI."""

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from zoneinfo import ZoneInfo

from .analytics import fail, local_day, numeric
from .planning import known_orders, moment, project_orders
from .scenario_decision import METRIC_SEMANTICS_VERSION, assess, measures


def project(points, initial, inbound, multiplier=1, delay=0, extra=0, arrival=None):
    cumulative_demand, cumulative_supply, served, peak = 0, initial, 0, 0
    series, first_risk = [], None
    capacity_demand, capacity_ratio = 0, None
    for point in points:
        day = date.fromisoformat(point["timestamp"])
        supply = sum(q for at, q in inbound if at + timedelta(days=delay) == day)
        if day == arrival:
            supply += extra
        demand = point["p50"] * multiplier
        cumulative_supply += supply
        capacity_demand += point.get("reference_p50", point["p50"])
        if capacity_demand > 1e-9:
            ratio = cumulative_supply / capacity_demand
            capacity_ratio = ratio if capacity_ratio is None else min(capacity_ratio, ratio)
        # Backordered quantities carry forward. Physical inventory is never negative.
        cumulative_demand += demand
        position = cumulative_supply - cumulative_demand
        physical = max(0, position)
        shortage = max(0, -position)
        served = min(cumulative_demand, cumulative_supply)
        if shortage > 0 and first_risk is None:
            first_risk = day.isoformat()
        peak = max(peak, shortage)
        series.append(
            {
                "day": day.isoformat(),
                "demand": demand,
                "inbound": supply,
                "net_position": position,
                "physical_inventory": physical,
                "unmet_cumulative": shortage,
            }
        )
    return {
        "series": series,
        "metrics": {
            "first_risk_date": first_risk,
            "peak_shortage": peak,
            "forecast_demand": cumulative_demand,
            "fulfilled_quantity": served,
            "fulfillment_percent": 100 * served / cumulative_demand if cumulative_demand else None,
            "known_orders_at_risk": None,
            "demand_headroom_percent": 100 * (capacity_ratio - 1)
            if capacity_ratio is not None
            else None,
        },
    }


def simulate(
    request,
    forecast,
    stocks,
    inbound_rows,
    snapshot_date,
    open_order_rows=None,
    commitment_rows=None,
    snapshot_at=None,
    source_names=None,
    include_decision=True,
):
    config = forecast["config"]
    selected_model = forecast.get("model", {}).get("entities", {}).get(request.sku, {})
    model_method = selected_model.get("method", forecast.get("model", {}).get("method", "unknown"))
    if request.require_ml and (
        model_method != "ridge_lag"
        or str(config.get("filters", {}).get("warehouse_id")) != str(request.warehouse_id)
        or open_order_rows is None
        or commitment_rows is None
    ):
        from fastapi import HTTPException

        raise HTTPException(
            422,
            detail={
                "code": "ML_SOURCE_REQUIRED",
                "message": "Cần tạo lại dự báo Machine Learning cho đúng sản phẩm và kho "
                "trước khi so sánh.",
            },
        )
    snapshot_at = snapshot_at or datetime.combine(
        snapshot_date, time.min, ZoneInfo(config["timezone"])
    ).astimezone(UTC)
    if (
        config["metric_type"] != "FLOW"
        or config["frequency"] != "D"
        or config["aggregation"] != "SUM"
        or config["entity_column"] != "sku"
        or forecast["evidence"]["data_products"][0]["name"]
        not in {
            (source_names or {}).get("sales_history", "sales.confirmed_demand_daily"),
            (source_names or {}).get("demand", "sales.planning_demand_daily"),
        }
    ):
        fail("Inventory planning requires daily SKU demand in product units")
    original_points = sorted(
        [p for p in forecast["points"] if p["item_id"] == request.sku], key=lambda p: p["timestamp"]
    )
    # Quantiles select a planning assumption, not a calibrated stockout probability.
    points = [
        {**p, "reference_p50": p["p50"], "p50": p[request.demand_quantile]} for p in original_points
    ]
    if (
        not points
        or any(p["p50"] < 0 for p in points)
        or any(p["p50"] < 0 for p in original_points)
    ):
        fail("No valid nonnegative demand forecast for SKU")
    start = date.fromisoformat(points[0]["timestamp"])
    if start != snapshot_date:
        fail("Forecast must begin on today's operational snapshot date; refresh configuration")
    end = date.fromisoformat(points[-1]["timestamp"])
    if request.additional_quantity and not start <= request.arrival_date <= end:
        fail("Additional supply must arrive inside the forecast horizon")
    stock = [
        r
        for r in stocks
        if r["sku"] == request.sku and str(r["warehouse_id"]) == str(request.warehouse_id)
    ]
    if len(stock) != 1:
        fail("Select one published warehouse/SKU stock snapshot")
    row = stock[0]
    if row["uom"] != config["unit"]:
        fail("Forecast and stock UOM mismatch")
    initial = numeric(row["on_hand_quantity"]) - numeric(row["reserved_quantity"])
    if initial < 0 or numeric(row["reserved_quantity"]) < 0:
        fail("Invalid stock/reservation balance")
    inbound, overdue = [], 0
    for row in inbound_rows:
        if row["sku"] != request.sku or str(row["warehouse_id"]) != str(request.warehouse_id):
            continue
        if row["uom"] != config["unit"]:
            fail("Inbound UOM mismatch")
        if row["status"] not in {"APPROVED", "PARTIALLY_RECEIVED"}:
            continue
        at = local_day(row["expected_at"], config["timezone"])
        quantity = numeric(row["remaining_quantity"])
        if quantity < 0:
            fail("Invalid remaining inbound quantity")
        if at < start or moment(row["expected_at"]) < snapshot_at:
            overdue += 1
            continue  # Overdue plans are not receipts and never become opening stock.
        inbound.append((at, quantity))
    orders_available = open_order_rows is not None and commitment_rows is not None
    warehouse_stocks = [r for r in stocks if str(r["warehouse_id"]) == str(request.warehouse_id)]
    warehouse_inbound = [
        r for r in inbound_rows if str(r["warehouse_id"]) == str(request.warehouse_id)
    ]
    orders = (
        known_orders(open_order_rows, commitment_rows, request.warehouse_id, config["timezone"])
        if orders_available
        else None
    )

    def evaluate(series, multiplier, delay, extra=0, arrival=None):
        if orders_available:
            return project_orders(
                series,
                warehouse_stocks,
                warehouse_inbound,
                orders,
                request.sku,
                config["unit"],
                start,
                multiplier=multiplier,
                delay=delay,
                extra=extra,
                arrival=arrival,
                timezone=config["timezone"],
                snapshot_at=snapshot_at,
            )
        return project(series, initial, inbound, multiplier, delay, extra, arrival)

    baseline = evaluate(points, request.demand_multiplier, request.inbound_delay_days)
    proposed = evaluate(
        points,
        request.demand_multiplier,
        request.inbound_delay_days,
        request.additional_quantity,
        request.arrival_date,
    )
    reference = evaluate(original_points, 1, 0) if include_decision else None
    before, after = measures(baseline), measures(proposed)
    delta = {
        key: proposed["metrics"][key] - baseline["metrics"][key]
        for key in ("forecast_demand", "fulfilled_quantity")
    }
    delta["peak_shortage"] = after["peak_shortage"] - before["peak_shortage"]
    result = {
        "baseline": baseline,
        "proposed": proposed,
        "comparison": {"metrics_delta": delta},
        "evidence": {
            "metric_semantics_version": METRIC_SEMANTICS_VERSION,
            "shortage_basis": before["shortage_basis"],
            "calculation_method": "deterministic supply-demand projection",
            "comparison_basis": "Both sides use the same demand and inbound-delay assumptions; "
            "only proposed adds the requested extra supply",
            "data_products": [
                *forecast["evidence"]["data_products"],
                request.stock.model_dump(mode="json"),
                request.inbound.model_dump(mode="json"),
            ],
            "assumptions": request.model_dump(mode="json"),
            "opening_available": initial,
            "unit": config["unit"],
            "timezone": config["timezone"],
            "config_version": forecast["evidence"].get("config_version", 1),
            "model": forecast.get("model", {}),
            "executed_at": datetime.now(UTC).isoformat(),
            "snapshot_date": snapshot_date.isoformat(),
            "snapshot_at": snapshot_at.isoformat(),
            "stock_updated_at": stock[0].get("updated_at"),
            "input_sha256": hashlib.sha256(
                json.dumps(
                    {
                        "stock": stocks,
                        "inbound": inbound_rows,
                        "forecast": forecast["points"],
                        "open_orders": open_order_rows,
                        "commitments": commitment_rows,
                    },
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest(),
            "excluded_overdue_inbound_plans": overdue,
            "formulas": {
                "available": "on_hand − reserved",
                "new_demand": (
                    f"{request.demand_quantile} × demand_multiplier; "
                    "pointwise planning assumption, not service probability"
                ),
                "net_position": (
                    "opening_available + cumulative approved remaining inbound "
                    "− cumulative forecast demand"
                ),
                "physical_inventory": "max(0, net_position)",
                "shortage": "max(0, −net_position); unmet demand carried forward",
            },
            "limitations": [
                "Reserved stock is protected and excluded from available supply",
                "Forecast demand represents future new demand, not known backlog",
                "Published open_orders lacks fulfilled/reserved quantities; "
                "unallocated backlog and order lateness are unknown",
                "Stock is shared by warehouse; demand allocation to the selected warehouse "
                "is an explicit scenario assumption",
                "Inbound is a plan; physical receipts remain uncertain",
                "Daily projection assumes inbound before that day's demand; no intraday deadlines",
                "No product-quantity to dispatch conversion; capacity analyzed independently",
            ],
        },
    }
    if orders_available:
        result["evidence"]["data_products"].extend(
            [
                request.open_orders.model_dump(mode="json"),
                request.commitments.model_dump(mode="json"),
            ]
        )
        result["evidence"]["formulas"]["unallocated_backlog"] = (
            "ordered − dispatched − active held; known orders consume stock all-or-nothing"
        )
        result["evidence"]["formulas"]["net_position"] += " − cumulative unallocated known demand"
        result["evidence"]["formulas"]["physical_inventory"] = (
            "opening_available + inbound − known_fulfilled_quantity − forecast_fulfilled_quantity "
            "(all cumulative, protected holds excluded)"
        )
        result["evidence"]["formulas"]["shortage"] = (
            "unmet_known_quantity + unmet_forecast_quantity; may include whole-order blocking"
        )
        result["evidence"]["limitations"] = [
            "Reserved stock remains protected; dispatched quantities are not counted again",
            "Known unallocated orders have priority over new demand, ordered by deadline/id",
            "All unallocated lines must fit together; no partial reservation is simulated",
            "Risk count retains any known unallocated order that missed its deadline",
            "Only selected SKU has new forecast demand; other SKU stock covers known orders only",
            "Published inbound ETAs and known order deadlines use exact timestamps",
            "New forecast demand is assessed at day end; future order timestamps are unknown",
            "Additional supply dates mean day start, or snapshot time for today's arrival",
            "Known unallocated demand is assessed at its deadline; missed deadlines stay at risk",
            "Physical inventory is the free physical pool; existing protected holds are excluded",
            "No product-to-dispatch conversion; carrier capacity remains an independent constraint",
        ]
    before_orders = {o["order_id"] for o in baseline.get("orders", []) if o["at_risk"]}
    after_orders = {o["order_id"] for o in proposed.get("orders", []) if o["at_risk"]}
    result["evidence"]["business_summary"] = {
        "shortage_before": before["peak_shortage"],
        "shortage_after": after["peak_shortage"],
        "reduced_shortage": before["peak_shortage"] - after["peak_shortage"],
        "risk_date_before": before["first_shortage_date"],
        "risk_date_after": after["first_shortage_date"],
        "metric_semantics_version": METRIC_SEMANTICS_VERSION,
        "shortage_basis": before["shortage_basis"],
        "orders_resolved": len(before_orders - after_orders),
        "orders_remaining": len(after_orders),
        "notes": [
            "Phương án chỉ là tính thử, chưa tạo kế hoạch nhập hay phiếu nhận hàng.",
            "Lượng hàng về sau hạn không giải quyết được các đơn đã trễ trước đó."
            if after_orders
            else "Chưa phát hiện đơn còn trễ do thiếu hàng trong phạm vi tính.",
            "Lịch vận chuyển cần kiểm tra riêng; thêm hàng không tự xếp lịch bàn giao.",
        ],
    }
    if include_decision:
        result["reference"] = reference
        result["decision"] = assess(
            reference,
            baseline,
            proposed,
            lambda quantity, arrival: evaluate(
                points, request.demand_multiplier, request.inbound_delay_days, quantity, arrival
            ),
            model_method,
            request.additional_quantity,
        )
        result["evidence"]["decision_version"] = "three_case_supply_deficit_v2"
        result["evidence"]["formulas"]["demand_headroom"] = (
            "100 × (min_t((opening + arrivals_through_t − known_due_through_t) / "
            "original_ML_P50_through_t) − 1); undefined for no positive forecast "
            "or known-order supply deficit even at zero new demand"
        )
        result["evidence"]["decision_basis"] = (
            "Reference uses original model P50 and original inbound dates. Stressed/proposed "
            "share selected demand quantile, multiplier and inbound delay; "
            "only proposed adds supply. "
            "Minimum quantity offsets the largest event-time physical supply deficit; "
            "arrival is at the beginning of the first deficit day, or snapshot time today. "
            "No supplier lead time, cost optimum or unique-customer forecast is inferred."
        )
    return result


def sensitivity(
    request,
    forecast,
    stocks,
    inbounds,
    snapshot_date,
    open_orders=None,
    commitments=None,
    *,
    snapshot_at,
    reference_result,
    source_names=None,
):
    """A bounded decision grid using the same planner, inputs and demand assumptions.

    Each cell is an independent proposal, never an accumulated purchase. No new
    facts, forecast fits, random draws, probabilities or source reads are created.
    """
    selected = [p for p in forecast["points"] if p["item_id"] == request.sku]
    start = min(date.fromisoformat(p["timestamp"]) for p in selected)
    end = max(date.fromisoformat(p["timestamp"]) for p in selected)
    risk = reference_result["baseline"]["metrics"]["first_risk_date"]
    at_risk = date.fromisoformat(risk) if risk else start + (end - start) // 2
    dates = sorted(
        {
            d
            for d in (start, end, request.arrival_date, at_risk, at_risk - timedelta(days=1))
            if start <= d <= end
        }
    )
    shortage = measures(reference_result["baseline"])["peak_shortage"]
    quantities = sorted(
        {
            0,
            request.additional_quantity,
            *(min(1e9, ceil(shortage * ratio)) for ratio in (0.5, 1, 1.5)),
        }
    )
    cells = []
    for quantity in quantities:
        for arrival in dates:
            proposal = request.model_copy(
                update={
                    "additional_quantity": quantity,
                    "arrival_date": arrival,
                    "include_sensitivity": False,
                    "save": False,
                }
            )
            result = simulate(
                proposal,
                forecast,
                stocks,
                inbounds,
                snapshot_date,
                open_orders,
                commitments,
                snapshot_at=snapshot_at,
                source_names=source_names,
                include_decision=False,
            )["proposed"]
            cells.append(
                {
                    "quantity": quantity,
                    "arrival_date": arrival.isoformat(),
                    "peak_shortage": measures(result)["peak_shortage"],
                    "known_orders_at_risk": result["metrics"]["known_orders_at_risk"],
                    "first_risk_date": result["metrics"]["first_risk_date"],
                }
            )
    return {
        "quantities": quantities,
        "dates": [d.isoformat() for d in dates],
        "cells": cells,
        "unit": forecast["config"]["unit"],
    }
