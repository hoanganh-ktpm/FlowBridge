# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from datetime import date

import pytest
from workspace_app.learning import p50_holdout, validated_forecast

from .test_business_insights import training


def test_bias_corrected_quality_uses_a_disjoint_final_time_block():
    config, data = training()
    before = deepcopy(data)
    report = p50_holdout(data["points"], config, 0)
    for score in report.values():
        assert score["training_end"] == score["calibration_end"]
        assert score["calibration_end"] < score["validation_start"] <= score["validation_end"]
        assert score["observations"] == config.forecast_horizon
        expected = (
            sum(abs(a - p) for a, p in zip(score["actuals"], score["predictions"], strict=True))
            / config.forecast_horizon
        )
        assert score["mae"] == pytest.approx(expected)
    assert data == before


def test_final_holdout_targets_cannot_change_training_calibration_or_predictions():
    config, data = training()
    first = p50_holdout(data["points"], config, 0)
    for point in data["points"][-config.forecast_horizon :]:
        point["target"] += 10000
    changed = p50_holdout(data["points"], config, 0)
    for method in first:
        assert first[method]["predictions"] == changed[method]["predictions"]
        assert changed[method]["mae"] > first[method]["mae"]


def test_unseen_gap_is_kept_in_quality_report():
    config, data = training()
    for score in p50_holdout(data["points"], config, 3).values():
        assert (
            date.fromisoformat(score["validation_start"])
            - date.fromisoformat(score["training_end"])
        ).days == 4


def test_short_history_has_no_invented_post_calibration_score():
    config, data = training(count=80)
    assert p50_holdout(data["points"], config, 0) is None
    _, model = validated_forecast(data, config)
    assert model["entities"]["A"]["p50_holdout"] is None


def test_operational_forecast_keeps_base_validation_and_separate_p50_quality():
    config, data = training()
    points, model = validated_forecast(data, config)
    item = model["entities"]["A"]
    assert item["validation"] and item["p50_holdout"]
    assert all(p["p10"] <= p["p50"] <= p["p90"] for p in points)
    assert "not a guarantee" in model["p50_holdout_basis"]
