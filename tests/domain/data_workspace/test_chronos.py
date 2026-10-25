# SPDX-License-Identifier: GPL-3.0-or-later
"""Chronos boundary tests. Pipeline fixtures are not real inference evidence."""

import hashlib
import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from workspace_app.analytics import prepare
from workspace_app.forecasting import Forecaster, statistical_forecast
from workspace_app.runtime import PREFIX, create_app
from workspace_app.schemas import AnalysisConfig, ForecastRequest, PlanningRequest

from .test_api import RepositoryBoundary
from .test_decision_workspace import PlanningBoundary
from .test_pipeline import config, product, rows


@pytest.fixture
def provider(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")
    revision = "a" * 40
    files = {
        "config.json": "{}",
        "README.md": "---\nlicense: apache-2.0\n---\nModel card fixture",
        "LICENSE": "Apache License Version 2.0",
        "model.safetensors": "test fixture, not model weights",
    }
    for name, content in files.items():
        (tmp_path / name).write_bytes(content.encode("utf-8"))
    manifest = {
        "repository": "amazon/chronos-2",
        "revision": revision,
        "weights_license": "Apache-2.0",
        "sha256": {n: hashlib.sha256(c.encode()).hexdigest() for n, c in files.items()},
    }
    (tmp_path / "model_provenance.json").write_text(json.dumps(manifest))
    calls, loads = [], []

    class Chronos2Pipeline:
        corrupt = None

        def predict_df(
            self, frame, prediction_length, quantile_levels, id_column, timestamp_column, target
        ):
            calls.append(frame.copy())
            result = []
            for entity, group in frame.groupby(id_column):
                frequency = pd.infer_freq(group[timestamp_column])
                for i in range(1, prediction_length + 1):
                    result.append(
                        {
                            id_column: entity,
                            timestamp_column: pd.date_range(
                                group[timestamp_column].max(), periods=i + 1, freq=frequency
                            )[-1],
                            "0.1": -1,
                            "0.5": 10,
                            "0.9": 20,
                        }
                    )
            if self.corrupt == "duplicate":
                result.append(result[0])
            if self.corrupt == "nonfinite":
                result[0]["0.5"] = float("nan")
            if self.corrupt == "missing":
                result.pop()
            return pd.DataFrame(result)

    pipeline = Chronos2Pipeline()

    def load(path, **kwargs):
        loads.append((path, kwargs))
        assert kwargs == {"device_map": "cpu", "local_files_only": True}
        return pipeline

    monkeypatch.setitem(
        sys.modules,
        "chronos",
        SimpleNamespace(
            BaseChronosPipeline=SimpleNamespace(from_pretrained=load),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            inference_mode=nullcontext,
            set_num_threads=lambda _: None,
            get_num_threads=lambda: 8,
        ),
    )
    monkeypatch.setattr("workspace_app.forecasting.version", lambda _: "2.3.2-fixture")
    return Forecaster(tmp_path, revision), pipeline, calls, loads


def test_pretrained_forecast_keeps_calendar_gap_and_evaluates_unseen_blocks(provider):
    forecaster, _, calls, loads = provider
    cfg = config(
        end="2026-03-01", forecast_model="chronos2", forecast_horizon=7, forecast_start="2026-03-04"
    )
    dataset = prepare(cfg, rows(cfg), product(cfg))
    points, model = forecaster.run(dataset, cfg)
    assert len(points) == 14 and len(calls) == 3 and len(loads) == 1
    assert points[0]["timestamp"] == "2026-03-04" and points[-1]["timestamp"] == "2026-03-10"
    assert all(p["p10"] == 0 <= p["p50"] <= p["p90"] for p in points)
    assert model["pretrained"] is True and model["fine_tuning"] is False
    assert model["method"] == "chronos2" and model["model"] == "amazon/chronos-2"
    assert model["artifact_manifest_sha256"] and model["revision"] == "a" * 40
    score = model["entities"]["A"]["validation"]["chronos2"]
    assert len(score["windows"]) == 2
    for frame, window in zip(calls[1:], score["windows"], strict=True):
        assert frame["timestamp"].max().date().isoformat() == window["training_end"]
        assert window["training_end"] < window["validation_start"]
    forecaster.run(dataset, cfg)
    assert len(loads) == 1


@pytest.mark.parametrize("corrupt", ["duplicate", "nonfinite", "missing"])
def test_bad_provider_output_never_silently_becomes_statistics(provider, corrupt):
    forecaster, pipeline, calls, _ = provider
    pipeline.corrupt = corrupt
    cfg = config(forecast_model="chronos2")
    with pytest.raises(HTTPException) as error:
        forecaster.run(prepare(cfg, rows(cfg), product(cfg)), cfg)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "CHRONOS_UNAVAILABLE"
    assert len(calls) == 1


def test_short_context_forecasts_without_inventing_validation_scores(provider):
    forecaster, _, calls, _ = provider
    cfg = config(end="2026-01-14", forecast_model="chronos2", forecast_horizon=30)
    points, model = forecaster.run(prepare(cfg, rows(cfg), product(cfg)), cfg)
    assert len(points) == 60 and len(calls) == 1
    assert model["entities"]["A"]["validation"] == {}


def test_changed_weights_are_rejected_before_pipeline_load(provider):
    forecaster, _, _, loads = provider
    (forecaster.model_path / "model.safetensors").write_text("corrupted")
    cfg = config(forecast_model="chronos2")
    with pytest.raises(HTTPException) as error:
        forecaster.run(prepare(cfg, rows(cfg), product(cfg)), cfg)
    assert error.value.status_code == 503 and loads == []


@pytest.mark.parametrize(
    "frequency,start,end,expected",
    [
        ("W", "2026-01-05", "2026-04-12", "2026-04-13"),
        ("M", "2025-01-01", "2026-02-28", "2026-03-01"),
    ],
)
def test_forecast_respects_complete_week_and_month_bucket_boundaries(
    provider, frequency, start, end, expected
):
    forecaster, _, _, _ = provider
    cfg = config(
        start=start, end=end, frequency=frequency, forecast_model="chronos2", forecast_horizon=2
    )
    points, _ = forecaster.run(prepare(cfg, rows(cfg), product(cfg)), cfg)
    assert len(points) == 4 and points[0]["timestamp"] == expected


def test_new_forecasts_and_plans_default_to_chronos_without_fallback():
    cfg = config().model_dump()
    cfg.pop("forecast_model")
    assert AnalysisConfig.model_validate(cfg).forecast_model == "chronos2"
    assert ForecastRequest(config=cfg).allow_statistical_fallback is False
    assert PlanningRequest(**PlanningBoundary().refs).forecast_model == "chronos2"


@pytest.mark.asyncio
async def test_chronos_planning_boundary_drives_scenarios_and_rechecks_saved_permissions():
    boundary, repository = PlanningBoundary(), RepositoryBoundary()
    methods = []

    class PipelineBoundary:
        def run(self, dataset, cfg, allow_fallback):
            assert cfg.forecast_model == "chronos2" and allow_fallback is False
            methods.append(cfg.forecast_model)
            points, _ = statistical_forecast(dataset, cfg)
            return points, {"method": "chronos2", "kind": "machine_learning", "pretrained": True}

    app = create_app(boundary, repository, PipelineBoundary())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://workspace",
        headers={"Authorization": "Bearer caller"},
    ) as client:
        response = await client.post(PREFIX + "/planning", json={**boundary.refs, "sku": "A"})
        assert response.status_code == 200, response.text
        plan = response.json()
        assert methods == ["chronos2"]
        assert plan["forecast"]["config"]["filters"]["warehouse_id"] == boundary.wh
        body = {
            "forecast_run_id": plan["forecast"]["id"],
            "sku": "A",
            "warehouse_id": boundary.wh,
            "arrival_date": plan["start"],
            "require_ml": True,
            "save": True,
            **{k: boundary.refs[k] for k in ["stock", "inbound", "open_orders", "commitments"]},
        }
        response = await client.post(PREFIX + "/scenarios", json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["decision"]["model_method"] == "chronos2"
        boundary.revoked = boundary.refs["stock"]["data_product_id"]
        assert (await client.get(PREFIX + "/scenarios/" + result["id"])).status_code == 403
