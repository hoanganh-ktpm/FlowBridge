# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, deterministic analytical pipeline; configuration never executes code."""

import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from statistics import mean, median, pstdev
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .decision_freshness import digest
from .schemas import AnalysisConfig


def fail(message):
    raise HTTPException(422, message)


def local_day(value, timezone):
    try:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        value = str(value)
        if len(value) == 10:
            return date.fromisoformat(value)
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            fail("Timestamp requires timezone")
        return moment.astimezone(ZoneInfo(timezone)).date()
    except (TypeError, ValueError):
        fail("Missing or invalid timestamp")


def bucket(day, frequency):
    if frequency == "W":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1) if frequency == "M" else day


def next_bucket(day, frequency):
    if frequency == "M":
        return date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return day + timedelta(days=7 if frequency == "W" else 1)


def numeric(value):
    try:
        result = float(value)
        if isinstance(value, bool) or not math.isfinite(result):
            fail("Invalid numeric value")
        return result
    except (TypeError, ValueError, OverflowError):
        fail("Invalid numeric value")


def prepare(config: AnalysisConfig, rows: list[dict], product: dict, *, known_event_entities=None):
    groups, seen, duplicates = defaultdict(list), set(), 0
    stock_grains = defaultdict(set)
    selected = []
    names = {f["name"] for f in product["fields"]}
    required = {config.time_column, config.entity_column, config.target_column}
    required.update(config.filters)
    if config.unit_column:
        required.add(config.unit_column)
    if not required <= names:
        fail("Mapping is incompatible with the authorized contract")
    # Reject incomplete first/last W/M buckets rather than forecast partial-period totals.
    if (
        bucket(config.start, config.frequency) != config.start
        or next_bucket(bucket(config.end, config.frequency), config.frequency)
        != config.end + timedelta(days=1)
    ) and config.frequency != "D":
        fail("Weekly/monthly periods must contain complete calendar buckets")
    for row in rows:
        if any(row.get(key) != value for key, value in config.filters.items()):
            continue
        day = local_day(row.get(config.time_column), config.timezone)
        if not config.start <= day <= config.end:
            continue
        entity_value = row.get(config.entity_column)
        if entity_value is None or str(entity_value).strip() == "":
            fail("Missing entity")
        entity = str(entity_value)
        if config.entities and entity not in config.entities:
            continue
        if config.unit_column and row.get(config.unit_column) != config.unit:
            fail("Unit mismatch; no implicit conversion")
        target = numeric(row.get(config.target_column))
        if config.metric_type == "STOCK":
            grain = tuple(
                row.get(key)
                for key in product.get("business_key", [])
                if key not in {"org_id", config.time_column}
            )
            stock_grains[entity].add(grain)
            if len(stock_grains[entity]) > 1:
                fail("STOCK mapping mixes source grains; constrain warehouse/product dimensions")
        raw_time = str(row[config.time_column])
        canonical_time = (
            datetime.fromisoformat(raw_time.replace("Z", "+00:00")).astimezone(UTC).isoformat()
            if len(raw_time) > 10
            else raw_time
        )
        key = (entity, canonical_time)
        if key in seen:
            duplicates += 1
            if config.duplicate_policy == "error" or config.metric_type == "STOCK":
                fail("Duplicate time/entity key; explicitly select aggregation if intentional")
        seen.add(key)
        groups[(entity, bucket(day, config.frequency))].append((canonical_time, target))
        selected.append(row)
    established = (
        (set(config.entities) & set(known_event_entities or []))
        if (config.metric_type == "FLOW" and config.missing_policy == "zero")
        else set()
    )
    if not groups and not established:
        fail("No data in selected period/entities")
    entities = sorted({key[0] for key in groups} | established)
    if config.entities and set(config.entities) - set(entities):
        fail("One or more selected entities have no data")
    points, missing = [], 0
    for entity in entities:
        day, previous = bucket(config.start, config.frequency), None
        while day <= config.end:
            values = groups.get((entity, day), [])
            if values:
                numbers = [value for _, value in values]
                target = {
                    "SUM": sum(numbers),
                    "COUNT": len(numbers),
                    "AVG": mean(numbers),
                    "LAST": max(values)[1],
                    "MIN": min(numbers),
                    "MAX": max(numbers),
                }[config.aggregation]
                previous = target
            else:
                missing += 1
                if config.missing_policy == "error":
                    fail("Missing periods; select an explicit missing-value policy")
                if config.missing_policy == "forward_fill" and previous is None:
                    fail("Cannot forward-fill without an earlier stock observation")
                target = 0 if config.missing_policy == "zero" else previous
            if not math.isfinite(target):
                fail("Aggregation overflow")
            points.append({"item_id": entity, "timestamp": day.isoformat(), "target": target})
            day = next_bucket(day, config.frequency)
    outliers = 0
    for entity in entities:
        values = [p["target"] for p in points if p["item_id"] == entity]
        center, spread = median(values), pstdev(values)
        outliers += sum(abs(value - center) > 3 * spread for value in values) if spread else 0
    return {
        "points": points,
        "preview_before": selected[:12],
        "preview_after": points[:12],
        "quality": {
            "source_rows": len(rows),
            "selected_rows": len(selected),
            "missing_periods": missing,
            "duplicate_keys": duplicates,
            "outliers_flagged_retained": outliers,
            "entity_count": len(entities),
        },
        "evidence": {
            "history_sha256": digest(points),
            "data_products": [
                {
                    "data_product_id": str(config.data_product_id),
                    "contract_version_id": str(config.contract_version_id),
                    "name": product["name"],
                    "application": product.get("application"),
                    "columns": sorted(required | {"org_id"} | set(product.get("business_key", []))),
                }
            ],
            "config": config.model_dump(mode="json"),
            "config_version": 1,
            "steps": [
                "Core contract/grant validation",
                "org isolation",
                "semantic mapping",
                "numeric/UOM validation",
                "calendar aggregation",
                config.missing_policy,
            ],
            "unit": config.unit,
            "timezone": config.timezone,
            "executed_at": datetime.now(UTC).isoformat(),
            "freshness": "Fetched on request; source update time unavailable unless exposed",
            "last_observation": max(p["timestamp"] for p in points),
            "assumptions": [
                "Outliers retained",
                "Missing policy is explicit",
                "Declared unit is an IT assertion when unit_column is absent",
                *(
                    ["Sales demand is gross confirmations, including subsequent cancellations"]
                    if product["name"].endswith("confirmed_demand_daily")
                    else []
                ),
            ],
        },
    }


def analyze(dataset):
    series, summaries = [], []
    for entity in sorted({p["item_id"] for p in dataset["points"]}):
        points = [p for p in dataset["points"] if p["item_id"] == entity]
        values = [p["target"] for p in points]
        for index, point in enumerate(points):
            series.append({**point, "moving_average": mean(values[max(0, index - 6) : index + 1])})
        flow = dataset["evidence"]["config"]["metric_type"] == "FLOW"
        current = sum(values[-7:]) if flow else mean(values[-7:])
        previous = sum(values[-14:-7]) if flow else (mean(values[-14:-7]) if len(values) > 7 else 0)
        growth = (current / previous - 1) * 100 if len(values) >= 14 and previous else None
        weekdays = defaultdict(list)
        for point in points:
            weekdays[date.fromisoformat(point["timestamp"]).weekday()].append(point["target"])
        xbar = (len(values) - 1) / 2
        denom = sum((i - xbar) ** 2 for i in range(len(values)))
        slope = (
            sum((i - xbar) * (v - mean(values)) for i, v in enumerate(values)) / denom
            if denom
            else 0
        )
        summaries.append(
            {
                "item_id": entity,
                "total": sum(values) if flow else None,
                "latest": values[-1],
                "recent_7_periods": current,
                "period_growth_percent": growth,
                "trend_per_period": slope,
                "standard_deviation": pstdev(values),
                "average_per_period": mean(values),
                "coefficient_of_variation_percent": (
                    100 * pstdev(values) / abs(mean(values)) if mean(values) else None
                ),
                "peak_weekday": (
                    max(weekdays, key=lambda key: mean(weekdays[key]))
                    if dataset["evidence"]["config"]["frequency"] == "D"
                    and len({mean(v) for v in weekdays.values()}) > 1
                    else None
                ),
                "day_of_week_average": (
                    {str(k): mean(v) for k, v in weekdays.items()}
                    if dataset["evidence"]["config"]["frequency"] == "D"
                    else {}
                ),
            }
        )
    return {
        "series": series,
        "metrics": summaries,
        "quality": dataset["quality"],
        "evidence": {
            **dataset["evidence"],
            "formulas": {
                "moving_average": "mean(last min(7,n) periods)",
                "period_growth_percent": (
                    "100 × (recent 7 / previous 7 − 1); "
                    "FLOW uses sums, STOCK means; null if undefined"
                ),
                "trend": "OLS slope across equally spaced periods",
                "total": "SUM(period values) for FLOW; not an inventory measure for STOCK",
                "variability": "100 × stddev / abs(mean); undefined at mean=0",
                "weekday_average": "mean(observed daily aggregate for each weekday)",
            },
        },
    }
