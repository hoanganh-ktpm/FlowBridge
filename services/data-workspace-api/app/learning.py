# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, deterministic supervised forecaster and chronological model selection.

Ridge is statistical learning, not a separate source of business facts. This
stdlib implementation fits actual lag/calendar features; it never loads weights
or sends data outside the governed deployment. Selection uses unseen time blocks.
"""

import hashlib
import json
from datetime import date, timedelta
from math import isfinite
from statistics import mean, pstdev, quantiles

from fastapi import HTTPException

from .forecasting import statistical_forecast

FEATURES = ["lag_1", "lag_7", "lag_14", "mean_7", "time", *[f"weekday_{i}" for i in range(6)]]


def solve(matrix, rhs):
    """Pivoted elimination on the positive-definite regularized normal system."""
    work = [list(row) + [value] for row, value in zip(matrix, rhs, strict=True)]
    n = len(rhs)
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(work[row][column]))
        work[column], work[pivot] = work[pivot], work[column]
        divisor = work[column][column]
        if abs(divisor) < 1e-12:
            raise ValueError("Singular learning system")
        work[column] = [value / divisor for value in work[column]]
        for row in range(n):
            if row == column:
                continue
            factor = work[row][column]
            work[row] = [a - factor * b for a, b in zip(work[row], work[column], strict=True)]
    return [row[-1] for row in work]


def features(values, index, day):
    return [
        values[index - 1],
        values[index - 7],
        values[index - 14],
        mean(values[index - 7 : index]),
        float(index),
        *[float(day.weekday() == i) for i in range(6)],
    ]


def ridge(history, origin, horizon):
    history = history[-180:]
    values = [p["target"] for p in history]
    if len(values) < 56:
        raise ValueError("At least 56 training days required")
    x = [
        features(values, i, date.fromisoformat(history[i]["timestamp"]))
        for i in range(14, len(values))
    ]
    y = values[14:]
    centers = [mean(row[j] for row in x) for j in range(len(FEATURES))]
    scales = [pstdev(row[j] for row in x) or 1 for j in range(len(FEATURES))]
    x = [[(v - centers[j]) / scales[j] for j, v in enumerate(row)] for row in x]
    intercept = mean(y)
    matrix = [
        [sum(row[i] * row[j] for row in x) + (1 if i == j else 0) for j in range(len(FEATURES))]
        for i in range(len(FEATURES))
    ]
    rhs = [
        sum(row[i] * (target - intercept) for row, target in zip(x, y, strict=True))
        for i in range(len(FEATURES))
    ]
    weights = solve(matrix, rhs)
    day = date.fromisoformat(history[-1]["timestamp"]) + timedelta(days=1)
    end = origin + timedelta(days=horizon - 1)
    nonnegative = min(values) >= 0
    result = []
    while day <= end:
        row = features(values, len(values), day)
        value = intercept + sum(
            w * (v - c) / s for w, v, c, s in zip(weights, row, centers, scales, strict=True)
        )
        if not isfinite(value):
            raise ValueError("Non-finite ML forecast")
        value = max(0, value) if nonnegative else value
        values.append(value)
        if day >= origin:
            result.append(value)
        day += timedelta(days=1)
    return result


def predict(method, history, config, origin, horizon):
    if method == "ridge_lag":
        return ridge(history, origin, horizon)
    effective = config.model_copy(update={"forecast_start": origin, "forecast_horizon": horizon})
    points, _ = statistical_forecast({"points": history}, effective)
    return [p["p50"] for p in points]


def validate(history, config, gap):
    horizon = config.forecast_horizon
    result = {}
    for method in ("seasonal_mean", "ridge_lag"):
        errors, actuals, windows = [], [], []
        for end in (len(history) - horizon, len(history)):
            cut = end - horizon - gap
            training, actual = history[:cut], history[end - horizon : end]
            predicted = predict(
                method, training, config, date.fromisoformat(actual[0]["timestamp"]), horizon
            )
            errors.extend(p["target"] - value for p, value in zip(actual, predicted, strict=True))
            actuals.extend(abs(p["target"]) for p in actual)
            windows.append(
                {
                    "training_end": training[-1]["timestamp"],
                    "validation_start": actual[0]["timestamp"],
                    "validation_end": actual[-1]["timestamp"],
                }
            )
        absolute = sum(abs(e) for e in errors)
        result[method] = {
            "mae": absolute / len(errors),
            "wape_percent": 100 * absolute / sum(actuals) if sum(actuals) else None,
            "errors": errors,
            "windows": windows,
        }
    return result


def validated_forecast(dataset, config):
    requested = config.forecast_model
    if config.frequency != "D":
        if requested == "ridge_lag":
            raise HTTPException(422, "Lag ML currently supports daily observations only")
        points, model = statistical_forecast(dataset, config)
        return points, {**model, "selection_reason": "Statistical method for weekly/monthly data"}
    output, decisions = [], {}
    for entity in sorted({p["item_id"] for p in dataset["points"]}):
        history = sorted(
            [p for p in dataset["points"] if p["item_id"] == entity], key=lambda p: p["timestamp"]
        )
        last = date.fromisoformat(history[-1]["timestamp"])
        origin = config.forecast_start or last + timedelta(days=1)
        gap = (origin - last).days - 1
        eligible = len(history) >= 56 + 2 * config.forecast_horizon + gap
        scores = validate(history, config, gap) if eligible else {}
        method = "seasonal_mean"
        reason = "Insufficient history for two unseen validation windows; use statistics"
        if requested == "ridge_lag" and not eligible:
            raise HTTPException(
                422,
                detail={
                    "code": "ML_HISTORY_SHORT",
                    "message": "Chưa đủ lịch sử kiểm chứng ML. Chọn Tự động hoặc thống kê.",
                },
            )
        if scores:
            method = (
                "ridge_lag"
                if requested == "ridge_lag"
                or (
                    requested == "auto"
                    and scores["ridge_lag"]["mae"] < 0.95 * scores["seasonal_mean"]["mae"]
                )
                else "seasonal_mean"
            )
            reason = (
                "Requested method"
                if requested != "auto"
                else "ML reduced unseen-block MAE by more than 5%"
                if method == "ridge_lag"
                else "Statistical baseline retained; ML did not improve unseen-block MAE by 5%"
            )
        central = predict(method, history, config, origin, config.forecast_horizon)
        if scores:
            residuals = quantiles(scores[method]["errors"], n=10, method="inclusive")
            low, median, high = residuals[0], residuals[4], residuals[-1]
            nonnegative = min(p["target"] for p in history) >= 0
            for i, value in enumerate(central):
                output.append(
                    {
                        "item_id": entity,
                        "timestamp": (origin + timedelta(days=i)).isoformat(),
                        "p10": max(0, value + low) if nonnegative else value + low,
                        "p50": max(0, value + median) if nonnegative else value + median,
                        "p90": max(0, value + high) if nonnegative else value + high,
                    }
                )
        else:
            points, _ = statistical_forecast({"points": history}, config)
            output.extend(points)
        decisions[entity] = {
            "method": method,
            "kind": "machine_learning" if method == "ridge_lag" else "statistical",
            "reason": reason,
            "observations": len(history),
            "gap_days": gap,
            "validation": {
                key: {k: v for k, v in score.items() if k != "errors"}
                for key, score in scores.items()
            },
            "training_input_sha256": hashlib.sha256(
                json.dumps(history, sort_keys=True).encode()
            ).hexdigest(),
        }
    methods = {decision["method"] for decision in decisions.values()}
    method = next(iter(methods)) if len(methods) == 1 else "mixed"
    return output, {
        "method": method,
        "requested_method": requested,
        "implementation_version": "2.0.0",
        "kind": "mixed"
        if method == "mixed"
        else "machine_learning"
        if method == "ridge_lag"
        else "statistical",
        "selection_version": "chronological_two_block_v1",
        "entities": decisions,
        "formula": "Ridge: min ||y - Xb||² + ||b||²; standardized lag/calendar features"
        if method == "ridge_lag"
        else "Weekday means plus empirical residual envelope",
        "interval": "Held-out residual quantiles; not calibrated future probabilities",
        "validation_basis": "Base predictions before held-out residual median bias correction",
        "features": FEATURES if "ridge_lag" in methods else [],
        "weights": "Fitted locally from authorized history; no pretrained weights"
        if "ridge_lag" in methods
        else None,
        "forecast_start": output[0]["timestamp"],
        "last_observation": max(p["timestamp"] for p in dataset["points"]),
    }
