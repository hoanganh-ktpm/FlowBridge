# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from workspace_app.analytics import analyze, prepare
from workspace_app.forecasting import Forecaster, statistical_forecast
from workspace_app.schemas import AnalysisConfig


def config(**kw):
    return AnalysisConfig.model_validate(
        {
            "data_product_id": str(uuid4()),
            "contract_version_id": str(uuid4()),
            "name": "Demand",
            "time_column": "day",
            "entity_column": "sku",
            "target_column": "quantity",
            "unit": "EA",
            "start": "2026-01-01",
            "end": "2026-01-28",
            "forecast_model": "seasonal_mean",
            **kw,
        }
    )


def product(cfg):
    return {
        "name": "sales.confirmed_demand_daily",
        "fields": [
            {"name": name}
            for name in [
                cfg.time_column,
                cfg.entity_column,
                cfg.target_column,
                *([cfg.unit_column] if cfg.unit_column else []),
            ]
        ],
    }


def rows(cfg):
    return [
        {
            cfg.time_column: (cfg.start + timedelta(days=i)).isoformat(),
            cfg.entity_column: entity,
            cfg.target_column: 10 + i % 7,
            **({cfg.unit_column: cfg.unit} if cfg.unit_column else {}),
        }
        for entity in ["A", "B"]
        for i in range((cfg.end - cfg.start).days + 1)
    ]


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {
            "entity_column": "carrier_code",
            "target_column": "accepted_shipments",
            "unit": "dispatches",
        },
    ],
)
def test_one_pipeline_analytics_forecast_reused_across_domains(mapping):
    cfg = config(**mapping)
    dataset = prepare(cfg, rows(cfg), product(cfg))
    result = analyze(dataset)
    assert result["metrics"][0]["total"] == 364
    assert result["metrics"][0]["period_growth_percent"] == 0
    assert result["series"][6]["moving_average"] == 13
    points, model = statistical_forecast(dataset, cfg)
    assert len(points) == 28 and model["kind"] == "statistical"
    assert points[0]["timestamp"] == "2026-01-29"
    assert all(p["p10"] <= p["p50"] <= p["p90"] for p in points)
    assert {p["item_id"] for p in points} == {"A", "B"}
    assert "weights" in model and model["weights"] is None


def test_forecast_changes_with_observations_and_excludes_future():
    cfg = config()
    source = rows(cfg)
    expected, _ = statistical_forecast(prepare(cfg, source, product(cfg)), cfg)
    source.append({"day": "2026-01-29", "sku": "A", "quantity": 10000000})
    actual, _ = statistical_forecast(prepare(cfg, source, product(cfg)), cfg)
    assert actual == expected
    for row in source:
        row["quantity"] *= 2
    larger, _ = statistical_forecast(prepare(cfg, source, product(cfg)), cfg)
    assert larger[0]["p50"] == 2 * expected[0]["p50"]


@pytest.mark.parametrize(
    "change",
    [
        {"metric_type": "STOCK", "aggregation": "SUM"},
        {"metric_type": "STOCK", "aggregation": "LAST", "missing_policy": "zero"},
        {"frequency": "invalid"},
        {"target_column": "__import__"},
        {"missing_policy": "forward_fill"},
        {"end": "2025-01-01"},
    ],
)
def test_invalid_semantics_are_rejected(change):
    with pytest.raises((ValidationError, ValueError)):
        config(**change)


def test_missing_explicit_policy_and_outliers_retained():
    cfg = config()
    source = rows(cfg)[1:]
    with pytest.raises(HTTPException, match="Missing periods"):
        prepare(cfg, source, product(cfg))
    cfg = cfg.model_copy(update={"missing_policy": "zero"})
    source[2]["quantity"] = 1e7
    result = prepare(cfg, source, product(cfg))
    assert result["quality"]["missing_periods"] == 1
    assert result["points"][0]["target"] == 0
    assert result["quality"]["outliers_flagged_retained"] > 0
    assert max(p["target"] for p in result["points"]) == 1e7


@pytest.mark.parametrize("value", [None, "text", "NaN", float("inf"), True])
def test_invalid_numbers_rejected(value):
    cfg = config()
    source = rows(cfg)
    source[0]["quantity"] = value
    with pytest.raises(HTTPException, match="numeric"):
        prepare(cfg, source, product(cfg))


def test_duplicates_units_and_stock_last():
    cfg = config(unit_column="uom")
    source = rows(cfg)
    source[0]["uom"] = "kg"
    with pytest.raises(HTTPException, match="Unit mismatch"):
        prepare(cfg, source, product(cfg))
    source = rows(cfg)
    source.append(source[0])
    with pytest.raises(HTTPException, match="Duplicate"):
        prepare(cfg, source, product(cfg))
    cfg = cfg.model_copy(update={"duplicate_policy": "aggregate"})
    assert prepare(cfg, source, product(cfg))["points"][0]["target"] == 20
    cfg = config(
        metric_type="STOCK",
        aggregation="LAST",
        frequency="W",
        start=date(2026, 1, 5),
        end=date(2026, 2, 1),
    )
    result = prepare(cfg, rows(cfg), product(cfg))
    assert len(result["points"]) == 8
    assert result["points"][0]["target"] == 16
    assert analyze(result)["metrics"][0]["total"] is None


@pytest.mark.parametrize(
    "aggregation,expected",
    [("SUM", 10), ("COUNT", 1), ("AVG", 10), ("LAST", 10), ("MIN", 10), ("MAX", 10)],
)
def test_supported_aggregations(aggregation, expected):
    cfg = config(aggregation=aggregation)
    assert prepare(cfg, rows(cfg), product(cfg))["points"][0]["target"] == expected


def test_month_complete_and_short_history():
    cfg = config(frequency="M", start="2026-01-01", end="2026-02-28")
    assert len(prepare(cfg, rows(cfg), product(cfg))["points"]) == 4
    cfg = config(end="2026-01-02")
    with pytest.raises(HTTPException) as error:
        statistical_forecast(prepare(cfg, rows(cfg), product(cfg)), cfg)
    assert error.value.detail["code"] == "INSUFFICIENT_HISTORY"


def test_chronos_absent_is_labelled_and_strict_request_fails():
    cfg = config(forecast_model="chronos2")
    data = prepare(cfg, rows(cfg), product(cfg))
    _, model = Forecaster().run(data, cfg, True)
    assert model["method"] == "seasonal_mean" and "fallback_reason" in model
    with pytest.raises(HTTPException) as error:
        Forecaster().run(data, cfg)
    assert error.value.status_code == 503


def test_stock_grain_cannot_silently_mix_warehouses_and_filter_is_configurable():
    cfg = config(
        metric_type="STOCK",
        aggregation="LAST",
        missing_policy="forward_fill",
        start="2026-01-01",
        end="2026-01-01",
    )
    data = [
        {"day": "2026-01-01", "sku": "A", "warehouse_id": "w1", "quantity": 10},
        {"day": "2026-01-01", "sku": "A", "warehouse_id": "w2", "quantity": 20},
    ]
    source = product(cfg)
    source["business_key"] = ["warehouse_id", "sku", "day"]
    source["fields"].append({"name": "warehouse_id"})
    with pytest.raises(HTTPException, match="mixes source grains"):
        prepare(cfg, data, source)
    cfg = cfg.model_copy(update={"filters": {"warehouse_id": "w2"}})
    assert prepare(cfg, data, source)["points"][0]["target"] == 20


def test_skewed_residuals_still_produce_ordered_quantiles():
    cfg = config(frequency="W", start="2025-01-06", end="2026-02-01")
    data = {
        "points": [
            {
                "item_id": "A",
                "timestamp": (cfg.start + timedelta(weeks=i)).isoformat(),
                "target": 0 if i == 0 else 10,
            }
            for i in range(56)
        ]
    }
    points, model = statistical_forecast(data, cfg)
    assert all(p["p10"] <= p["p50"] <= p["p90"] for p in points)
    assert points[0]["p50"] == 10
    assert model["implementation_version"] == "1.1.0"
