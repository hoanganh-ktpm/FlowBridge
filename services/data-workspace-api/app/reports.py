# SPDX-License-Identifier: GPL-3.0-or-later
"""Operational reports over two published event-aggregate contracts.

These views GROUP BY actual confirmations/acceptances. A missing date is zero
events in a successfully read complete source, not an imputed missing measure.
This rule is deliberately unavailable to arbitrary custom analytical mappings.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .analytics import analyze, bucket, local_day, next_bucket, prepare
from .schemas import AnalysisConfig


def recipe(product, source_names=None):
    names = source_names or {}
    if product["name"] == names.get("sales_history", "sales.confirmed_demand_daily"):
        return "Nhu cầu bán hàng", "sku", "confirmed_quantity", "EA"
    if product["name"] == names.get("transport_history", "logistics.accepted_shipments_daily"):
        return "Vận chuyển", "carrier_code", "accepted_shipments", "shipments"
    raise HTTPException(
        422,
        detail={
            "code": "UNSUPPORTED_REPORT",
            "message": "Nguồn này chưa có báo cáo nghiệp vụ. Liên hệ quản trị viên để cấu hình.",
        },
    )


def overview(product, rows, body, today=None, mapping=None, source_names=None):
    title, entity_column, target_column, unit = (
        (mapping.name, mapping.entity_column, mapping.target_column, mapping.unit)
        if mapping
        else recipe(product, source_names)
    )
    today = (
        today or datetime.now(ZoneInfo(mapping.timezone if mapping else "Asia/Ho_Chi_Minh")).date()
    )
    # Never infer zero from an unavailable source: connector errors propagate first.
    time_column = mapping.time_column if mapping else "day"
    timezone = mapping.timezone if mapping else "Asia/Ho_Chi_Minh"
    metric_type = mapping.metric_type if mapping else "FLOW"
    filtered = [
        r for r in rows if not mapping or all(r.get(k) == v for k, v in mapping.filters.items())
    ]
    observed = [(row, local_day(row[time_column], timezone)) for row in filtered]
    observed = [
        (row, day)
        for row, day in observed
        if day < today or (metric_type == "STOCK" and day == today)
    ]
    if not observed:
        raise HTTPException(
            422,
            detail={
                "code": "NO_DATA",
                "message": "Nguồn chưa có số liệu cho ngày đã hoàn tất.",
            },
        )
    entities = sorted({str(row[entity_column]) for row, _ in observed})
    entity = body.entity or entities[0]
    selected = [(row, day) for row, day in observed if str(row[entity_column]) == entity]
    if not selected:
        raise HTTPException(
            422,
            detail={
                "code": "NO_DATA",
                "message": "Chưa có số liệu cho lựa chọn này.",
            },
        )
    first = min(day for _, day in observed)
    end = max(day for _, day in observed)
    start = max(first, end - timedelta(days=body.days - 1))
    if mapping:
        if mapping.frequency != "D":
            end = bucket(end + timedelta(days=1), mapping.frequency) - timedelta(days=1)
            start = max(first, end - timedelta(days=body.days - 1))
            aligned = bucket(start, mapping.frequency)
            start = next_bucket(aligned, mapping.frequency) if aligned < start else aligned
            if start > end:
                raise HTTPException(
                    422,
                    detail={
                        "code": "NO_DATA",
                        "message": "Chưa có kỳ lịch đầy đủ trong khoảng này.",
                    },
                )
        config = AnalysisConfig.model_validate(
            {
                **mapping.model_dump(),
                "start": start,
                "end": end,
                "entities": [entity],
                "forecast_start": today
                if end < today and (today - end).days <= 90 and mapping.frequency == "D"
                else None,
            }
        )
    else:
        config = AnalysisConfig(
            name=title,
            data_product_id=body.data_product_id,
            contract_version_id=body.contract_version_id,
            time_column="day",
            entity_column=entity_column,
            target_column=target_column,
            unit=unit,
            unit_column="uom" if any(f["name"] == "uom" for f in product["fields"]) else None,
            metric_type="FLOW",
            aggregation="SUM",
            frequency="D",
            missing_policy="zero",
            duplicate_policy="error",
            start=start,
            end=end,
            entities=[entity],
            # Old historical sources can still be inspected. Forecast validation remains
            # strict and the UI disables operational predictions for sources >90 days old.
            forecast_start=today if (today - end).days <= 90 else None,
        )
    data = prepare(config, rows, product, known_event_entities=entities if not mapping else None)
    data["evidence"]["report_policy"] = {
        "method": "saved_mapping_v1" if mapping else "published_event_aggregate_v1",
        "missing_dates": config.missing_policy
        if mapping
        else "Zero events in the complete authorized aggregate view",
        "basis": "Sales confirmations / Logistics acceptances grouped by local calendar day",
        "period": "Latest completed source day; no invented recent observations",
    }
    compared = list(
        dict.fromkeys(body.compare_entities or [entity, *[e for e in entities if e != entity]][:3])
    )
    if any(e not in entities for e in compared):
        raise HTTPException(
            422,
            detail={
                "code": "NO_DATA",
                "message": "Đối tượng so sánh chưa có số liệu trong nguồn này.",
            },
        )
    comparison_config = config.model_copy(update={"entities": compared})
    comparison = analyze(
        prepare(
            comparison_config, rows, product, known_event_entities=entities if not mapping else None
        )
    )
    comparison["evidence"]["report_policy"] = data["evidence"]["report_policy"]
    return {
        "comparison": comparison,
        "comparison_entities": compared,
        "config": config.model_dump(mode="json"),
        "analytics": analyze(data),
        "entities": entities,
        "entity": entity,
        "source_start": first,
        "source_end": end,
        "source_age_days": (today - end).days,
    }
