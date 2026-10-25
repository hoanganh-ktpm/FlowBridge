# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from workspace_app.analytics import prepare
from workspace_app.connector import Context
from workspace_app.learning import ridge, validate, validated_forecast
from workspace_app.portfolio import carrier_summary, supply_reference
from workspace_app.runtime import PREFIX, create_app, forecast_config_at_request
from workspace_app.schemas import ForecastRequest
from workspace_app.simulation import simulate

from .test_api import ORG, OWNER, CoreBoundary, RepositoryBoundary
from .test_pipeline import config, product, rows
from .test_simulation import inputs


def test_cautious_plan_uses_daily_p90_without_mutating_forecast_or_claiming_service_probability():
    body, forecast, stock, inbound = inputs()
    forecast["points"] = [{**p, "p90": p["p50"] * 2} for p in forecast["points"]]
    normal = simulate(body, forecast, stock, inbound, date(2026, 10, 24))
    cautious = simulate(
        body.model_copy(update={"demand_quantile": "p90"}),
        forecast,
        stock,
        inbound,
        date(2026, 10, 24),
    )
    assert (
        cautious["baseline"]["metrics"]["forecast_demand"]
        == 2 * normal["baseline"]["metrics"]["forecast_demand"]
    )
    assert (
        cautious["baseline"]["metrics"]["peak_shortage"]
        >= normal["baseline"]["metrics"]["peak_shortage"]
    )
    assert forecast["points"][0]["p50"] == 10
    assert "not service probability" in cautious["evidence"]["formulas"]["new_demand"]


def training(count=180, trend=True):
    cfg = config(
        start="2026-01-01",
        end=(date(2026, 1, 1) + timedelta(days=count - 1)).isoformat(),
        forecast_model="auto",
    )
    data = [
        {
            "item_id": "A",
            "timestamp": (cfg.start + timedelta(days=i)).isoformat(),
            "target": 20 + (i * 2 if trend else 0) + (i % 7) * 3,
        }
        for i in range(count)
    ]
    return cfg, {"points": data}


def test_selected_calendar_window_is_inclusive_and_keeps_the_unseen_gap():
    cfg, data = training()
    today = cfg.end + timedelta(days=1)
    first, last = today + timedelta(days=3), today + timedelta(days=10)
    body = ForecastRequest(config=cfg, start_date=first, end_date=last)
    actual = forecast_config_at_request(body, datetime.combine(today, datetime.min.time(), UTC))
    assert actual.forecast_start == first and actual.forecast_horizon == 8
    points, _ = validated_forecast(data, actual.model_copy(update={"forecast_model": "ridge_lag"}))
    assert len(points) == 8
    assert points[0]["timestamp"] == first.isoformat()
    assert points[-1]["timestamp"] == last.isoformat()
    assert data["points"][-1]["timestamp"] == cfg.end.isoformat()
    assert cfg.forecast_start is None


@pytest.mark.parametrize("offset,end_offset", [(-1, 3), (1, 90)])
def test_selected_calendar_window_rejects_past_or_too_distant_dates(offset, end_offset):
    today = date(2026, 10, 25)
    body = ForecastRequest(
        config=config(start="2026-10-01", end="2026-10-24"),
        start_date=today + timedelta(days=offset),
        end_date=today + timedelta(days=end_offset),
    )
    with pytest.raises(HTTPException) as error:
        forecast_config_at_request(body, datetime(2026, 10, 24, 18, tzinfo=UTC))
    assert error.value.detail["code"] == "FORECAST_WINDOW_INVALID"


@pytest.mark.parametrize(
    "window",
    [
        {"start_date": "2026-10-25"},
        {"start_date": "2026-10-27", "end_date": "2026-10-26"},
        {"start_date": "2026-10-25", "end_date": "2026-10-31", "start_from_request_date": True},
    ],
)
def test_selected_calendar_window_requires_a_consistent_pair(window):
    with pytest.raises(ValueError):
        ForecastRequest(config=config(), **window)


def test_click_date_is_resolved_at_forecast_request_in_business_timezone():
    cfg = config(start="2026-10-01", end="2026-10-20", forecast_start="2026-10-25")
    body = ForecastRequest(config=cfg, start_from_request_date=True)
    actual = forecast_config_at_request(body, datetime(2026, 10, 25, 18, tzinfo=UTC))
    assert actual.forecast_start == date(2026, 10, 26)
    assert cfg.forecast_start == date(2026, 10, 25)  # no mutation of the applied report
    forecast, _ = validated_forecast(
        {
            "points": [
                {"item_id": "A", "timestamp": r["day"], "target": r["quantity"]}
                for r in rows(cfg)
                if r["sku"] == "A"
            ]
        },
        actual,
    )
    assert forecast[0]["timestamp"] == "2026-10-26"
    assert forecast[-1]["timestamp"] == "2026-11-08" and len(forecast) == 14


def test_explicit_generic_origin_remains_supported_and_stale_operational_history_is_rejected():
    cfg = config(forecast_start="2026-02-01")
    assert forecast_config_at_request(ForecastRequest(config=cfg), datetime.now(UTC)) is cfg
    with pytest.raises(HTTPException) as error:
        forecast_config_at_request(
            ForecastRequest(config=cfg, start_from_request_date=True),
            datetime(2026, 10, 25, tzinfo=UTC),
        )
    assert error.value.detail["code"] == "STALE_HISTORY"


def test_actual_lag_learner_beats_flat_seasonal_means_on_trending_unseen_blocks():
    cfg, data = training()
    points, model = validated_forecast(data, cfg)
    decision = model["entities"]["A"]
    assert decision["method"] == "ridge_lag" and model["kind"] == "machine_learning"
    assert (
        decision["validation"]["ridge_lag"]["mae"]
        < 0.95 * decision["validation"]["seasonal_mean"]["mae"]
    )
    assert len(points) == cfg.forecast_horizon
    assert all(0 <= p["p10"] <= p["p50"] <= p["p90"] for p in points)
    assert ridge(
        data["points"], date.fromisoformat(data["points"][-1]["timestamp"]) + timedelta(days=1), 3
    ) == ridge(
        data["points"], date.fromisoformat(data["points"][-1]["timestamp"]) + timedelta(days=1), 3
    )


def test_auto_keeps_exact_seasonal_baseline_instead_of_choosing_ml_for_its_name():
    cfg, data = training(trend=False)
    _, model = validated_forecast(data, cfg)
    assert model["method"] == "seasonal_mean"
    assert model["entities"]["A"]["validation"]["seasonal_mean"]["mae"] == 0


def test_ml_uses_the_requested_year_instead_of_silently_truncating_to_six_months():
    cfg, data = training(365)
    changed = [
        {**p, "target": p["target"] * 0.1} if i < 185 else dict(p)
        for i, p in enumerate(data["points"])
    ]
    origin = cfg.end + timedelta(days=1)
    assert ridge(changed, origin, 7) != pytest.approx(ridge(data["points"], origin, 7))


def test_short_history_uses_statistics_and_never_claims_ml_was_run():
    cfg, data = training(28)
    _, model = validated_forecast(data, cfg)
    assert model["kind"] == "statistical" and model["entities"]["A"]["validation"] == {}
    with pytest.raises(HTTPException) as error:
        validated_forecast(data, cfg.model_copy(update={"forecast_model": "ridge_lag"}))
    assert error.value.detail["code"] == "ML_HISTORY_SHORT"


def test_validation_windows_keep_the_same_gap_and_do_not_train_on_their_targets():
    cfg, data = training()
    cfg = cfg.model_copy(update={"forecast_start": cfg.end + timedelta(days=5)})
    scores = validate(data["points"], cfg, 4)
    for score in scores.values():
        for window in score["windows"]:
            assert (
                date.fromisoformat(window["validation_start"])
                - date.fromisoformat(window["training_end"])
            ).days == 5
            assert (
                date.fromisoformat(window["validation_end"])
                - date.fromisoformat(window["validation_start"])
            ).days == 13


def test_known_inactive_event_entity_can_show_zero_without_inventing_source_rows():
    cfg = config(entities=["A"], missing_policy="zero", start="2026-02-01", end="2026-02-28")
    raw = [{"day": "2026-01-01", "sku": "A", "quantity": 3}]
    data = prepare(cfg, raw, product(cfg), known_event_entities=["A"])
    assert data["quality"]["selected_rows"] == 0 and data["quality"]["missing_periods"] == 28
    assert all(p["target"] == 0 for p in data["points"])
    with pytest.raises(HTTPException):
        prepare(cfg, raw, product(cfg))


def portfolio_report():
    return {
        "config": {
            "entity_column": "sku",
            "metric_type": "FLOW",
            "frequency": "D",
            "aggregation": "SUM",
            "unit": "EA",
            "timezone": "Asia/Ho_Chi_Minh",
        },
        "comparison_entities": ["A"],
        "comparison": {"series": [{"item_id": "A", "target": 10} for _ in range(14)]},
    }


def test_cross_application_reference_joins_sku_uom_and_keeps_inbound_as_a_plan():
    now = datetime(2026, 10, 25, 3, tzinfo=UTC)
    stock = [
        {"sku": "A", "uom": "EA", "available_quantity": 10},
        {"sku": "A", "uom": "EA", "available_quantity": 20},
    ]
    inbound = [
        {
            "sku": "A",
            "uom": "EA",
            "status": "APPROVED",
            "remaining_quantity": 15,
            "expected_at": (now + timedelta(days=2)).isoformat(),
        },
        {
            "sku": "A",
            "uom": "EA",
            "status": "APPROVED",
            "remaining_quantity": 100,
            "expected_at": (now - timedelta(hours=1)).isoformat(),
        },
    ]
    result = supply_reference(portfolio_report(), stock, inbound, 14, now)
    row = result["rows"][0]
    assert row["available"] == 30 and row["planned_inbound"] == 15 and row["cover_days"] == 3
    assert row["reference_demand"] == 140 and row["reference_shortfall"] == 95
    assert row["overdue_plans_excluded"] == 1
    assert result["start"] == "2026-10-25" and result["end"] == "2026-11-07"
    assert "not a model forecast" in result["evidence"]["limitations"][0]


def test_missing_stock_or_unit_mismatch_is_unknown_not_zero():
    now = datetime.now(UTC)
    for stock in (None, [], [{"sku": "A", "uom": "KG", "available_quantity": 10}]):
        row = supply_reference(portfolio_report(), stock, [], 14, now)["rows"][0]
        assert (
            row["available"] is None
            and row["cover_days"] is None
            and row["planned_inbound"] is None
        )


def test_carrier_capacity_remains_in_dispatch_units_with_unknown_calendar_days():
    now = datetime(2026, 10, 25, 3, tzinfo=UTC)
    rows = [
        {
            "carrier_code": "C1",
            "capacity_date": "2026-10-25",
            "max_dispatches": 20,
            "used_dispatches": 5,
            "status": "ACTIVE",
        },
        {
            "carrier_code": "C1",
            "capacity_date": "2026-10-26",
            "max_dispatches": 100,
            "used_dispatches": 0,
            "status": "UNAVAILABLE",
        },
        {
            "carrier_code": "C1",
            "capacity_date": "2026-10-27",
            "max_dispatches": 2,
            "used_dispatches": 5,
            "status": "ACTIVE",
        },
    ]
    row = carrier_summary(rows, 14, now)["rows"][0]
    assert (
        row["remaining_slots"] == 15
        and row["booked_slots"] == 10
        and row["published_days"] == 3
        and row["overbooked_days"] == 1
    )


@pytest.mark.asyncio
async def test_private_saved_mapping_can_feed_a_report_without_new_product_specific_source_code():
    core, repo = CoreBoundary(), RepositoryBoundary()
    payload = {
        "config": core.cfg.model_dump(mode="json"),
        "sources": [
            {
                "ref": {
                    "data_product_id": str(core.cfg.data_product_id),
                    "contract_version_id": str(core.cfg.contract_version_id),
                },
                "columns": ["day", "sku", "quantity"],
            }
        ],
    }
    saved = await repo.save("config", Context("caller", ORG, OWNER, True), payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(core.respond)) as upstream:
        app = create_app(
            __import__("workspace_app.connector", fromlist=["CoreConnector"]).CoreConnector(
                upstream, "https://core.test", ORG
            ),
            repo,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://workspace",
            headers={"Authorization": "Bearer caller"},
        ) as client:
            body = {
                "data_product_id": str(core.cfg.data_product_id),
                "contract_version_id": str(core.cfg.contract_version_id),
                "configuration_id": saved["id"],
            }
            result = await client.post(PREFIX + "/overview", json=body)
            assert result.status_code == 200, result.text
            assert (
                result.json()["config"]["target_column"] == "quantity"
                and len(result.json()["comparison_entities"]) == 2
            )
            body["data_product_id"] = str(uuid4())
            assert (await client.post(PREFIX + "/overview", json=body)).status_code == 403
            other = await repo.save("config", Context("caller", ORG, uuid4(), True), payload)
            body["configuration_id"] = other["id"]
            body["data_product_id"] = str(core.cfg.data_product_id)
            assert (await client.post(PREFIX + "/overview", json=body)).status_code == 404


@pytest.mark.asyncio
async def test_training_lookback_does_not_pad_unknown_history_to_qualify_for_ml():
    core, repo = CoreBoundary(), RepositoryBoundary()
    cfg = core.cfg.model_copy(update={"missing_policy": "zero", "forecast_model": "ridge_lag"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(core.respond)) as upstream:
        app = create_app(
            __import__("workspace_app.connector", fromlist=["CoreConnector"]).CoreConnector(
                upstream, "https://core.test", ORG
            ),
            repo,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://workspace",
            headers={"Authorization": "Bearer caller"},
        ) as client:
            result = await client.post(
                PREFIX + "/forecasts",
                json={"config": cfg.model_dump(mode="json"), "training_days": 180},
            )
            assert (
                result.status_code == 422 and result.json()["detail"]["code"] == "ML_HISTORY_SHORT"
            )


@pytest.mark.asyncio
async def test_composite_rechecks_primary_and_never_reuses_revoked_stock():
    from workspace_app.schemas import ProductRef

    cfg = config(target_column="confirmed_quantity")
    main = ProductRef(
        data_product_id=cfg.data_product_id, contract_version_id=cfg.contract_version_id
    )
    stock_ref = ProductRef(data_product_id=uuid4(), contract_version_id=uuid4())

    class Boundary:
        stock_allowed = False
        primary_allowed = True
        revoke_during_related = False

        async def context(self, token):
            return Context(token, ORG, OWNER, False)

        async def product(self, ctx, ref):
            if ref.data_product_id == main.data_product_id:
                if not self.primary_allowed:
                    raise HTTPException(403)
                return {
                    "name": "sales.confirmed_demand_daily",
                    "fields": [{"name": n} for n in ["sku", "day", "confirmed_quantity"]],
                }
            return {
                "name": "warehouse.stock_availability",
                "fields": [
                    {"name": n} for n in ["sku", "uom", "warehouse_id", "available_quantity"]
                ],
            }

        async def rows(self, ctx, ref, columns=None, where=None):
            metadata = await self.product(ctx, ref)
            if ref.data_product_id == main.data_product_id:
                return metadata, rows(cfg)
            if not self.stock_allowed:
                raise HTTPException(403)
            if self.revoke_during_related:
                self.primary_allowed = False
            return metadata, [
                {"sku": "A", "uom": "EA", "warehouse_id": str(uuid4()), "available_quantity": 20}
            ]

        async def check(self, ctx, ref, columns=None):
            await self.product(ctx, ref)

    connector = Boundary()
    app = create_app(connector, RepositoryBoundary())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://workspace",
        headers={"Authorization": "Bearer caller"},
    ) as client:
        body = {**main.model_dump(mode="json"), "stock": stock_ref.model_dump(mode="json")}
        result = await client.post(PREFIX + "/overview", json=body)
        assert result.status_code == 200, result.text
        assert result.json()["related_issues"] == {"stock": 403} and result.json()["stocks"] == []
        assert result.json()["portfolio"]["rows"][0]["available"] is None
        connector.stock_allowed = True
        connector.revoke_during_related = True
        result = await client.post(PREFIX + "/overview", json=body)
        assert result.status_code == 403
