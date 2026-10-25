# SPDX-License-Identifier: GPL-3.0-or-later
"""Small numeric oracles for the analysis/visualization milestone."""

from datetime import timedelta

import pytest
from workspace_app.analytics import analyze, prepare

from .test_pipeline import config, product


def analyze_values(values, **options):
    cfg = config(end="2026-01-" + str(len(values)).zfill(2), **options)
    rows = [
        {"day": (cfg.start + timedelta(days=i)).isoformat(), "sku": "K120", "quantity": value}
        for i, value in enumerate(values)
    ]
    return analyze(prepare(cfg, rows, product(cfg)))


def test_seven_days_have_total_28_and_seven_period_average_4():
    actual = analyze_values([1, 2, 3, 4, 5, 6, 7])
    assert actual["metrics"][0]["total"] == 28
    assert actual["series"][-1]["moving_average"] == 4
    assert actual["metrics"][0]["period_growth_percent"] is None


def test_two_complete_weeks_have_twenty_percent_growth_and_zero_base_is_unknown():
    assert analyze_values([10] * 7 + [12] * 7)["metrics"][0]["period_growth_percent"] == (
        pytest.approx(20)
    )
    assert analyze_values([0] * 7 + [12] * 7)["metrics"][0]["period_growth_percent"] is None


def test_stock_snapshots_are_not_summed_into_inventory():
    actual = analyze_values([10, 12], metric_type="STOCK", aggregation="LAST")
    assert actual["metrics"][0]["total"] is None
    assert actual["metrics"][0]["latest"] == 12
