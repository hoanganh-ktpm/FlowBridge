# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import inspect
import json
import logging
import re
import threading
from collections import defaultdict
from datetime import date
from importlib.metadata import version
from math import isfinite
from pathlib import Path
from statistics import mean, quantiles

from fastapi import HTTPException

from .analytics import next_bucket

logger = logging.getLogger(__name__)


def statistical_forecast(dataset, config):
    points = []
    for entity in sorted({p["item_id"] for p in dataset["points"]}):
        history = [p for p in dataset["points"] if p["item_id"] == entity]
        if len(history) < 14:
            raise HTTPException(
                422,
                detail={
                    "code": "INSUFFICIENT_HISTORY",
                    "message": "Cần ít nhất 14 kỳ lịch sử hoàn chỉnh cho mỗi sản phẩm để dự đoán.",
                },
            )
        recent = history[-56:]
        seasonal = defaultdict(list)
        for point in recent:
            key = date.fromisoformat(point["timestamp"]).weekday() if config.frequency == "D" else 0
            seasonal[key].append(point["target"])
        centers = {key: mean(values) for key, values in seasonal.items()}
        residuals = [
            p["target"]
            - centers[
                date.fromisoformat(p["timestamp"]).weekday() if config.frequency == "D" else 0
            ]
            for p in recent
        ]
        deciles = quantiles(residuals, n=10, method="inclusive")
        day = config.forecast_start or next_bucket(
            date.fromisoformat(history[-1]["timestamp"]), config.frequency
        )
        nonnegative = min(p["target"] for p in history) >= 0
        for _ in range(config.forecast_horizon):
            center = centers[day.weekday() if config.frequency == "D" else 0]
            lower, upper = center + deciles[0], center + deciles[-1]
            central = center + deciles[4]
            if nonnegative:
                lower, central, upper = max(0, lower), max(0, central), max(0, upper)
            points.append(
                {
                    "item_id": entity,
                    "timestamp": day.isoformat(),
                    "p10": lower,
                    "p50": central,
                    "p90": upper,
                }
            )
            day = next_bucket(day, config.frequency)
    return points, {
        "method": "seasonal_mean",
        "implementation_version": "1.1.0",
        "kind": "statistical",
        "formula": "Weekday mean (D) or recent mean (W/M), plus empirical residual quantiles",
        "interval": "Inclusive residual P10/P50/P90; not calibrated future probabilities",
        "weights": None,
        "forecast_start": points[0]["timestamp"],
        "last_observation": max(p["timestamp"] for p in dataset["points"]),
    }


class Forecaster:
    """One locally provisioned model instance. Never downloads weights at request time."""

    def __init__(self, model_path=None, revision=None):
        self.model_path, self.revision = model_path, revision
        self.pipeline = None
        self.artifact_digest = None
        self.lock = threading.Lock()

    def run(self, dataset, config, allow_fallback=False):
        if config.forecast_model in {"seasonal_mean", "ridge_lag"}:
            from .learning import validated_forecast

            return validated_forecast(dataset, config)
        try:
            points, model = self.chronos(dataset, config)
            return points, model
        except (ImportError, OSError, ValueError, RuntimeError, TypeError, KeyError):
            logger.exception("Local Chronos-2 inference failed")
            if not allow_fallback:
                raise HTTPException(
                    503,
                    detail={
                        "code": "CHRONOS_UNAVAILABLE",
                        "message": "Chưa chạy được mô hình Chronos-2. "
                        "Cần kiểm tra thư viện và trọng số mô hình trên máy chủ.",
                    },
                ) from None
            points, model = statistical_forecast(dataset, config)
            model["fallback_reason"] = "Local pinned Chronos-2 unavailable or incompatible"
            model["requested_method"] = "chronos2"
            return points, model

    def chronos(self, dataset, config):
        histories = {
            entity: sorted(
                [p for p in dataset["points"] if p["item_id"] == entity],
                key=lambda p: p["timestamp"],
            )
            for entity in sorted({p["item_id"] for p in dataset["points"]})
        }
        if not histories or any(len(history) < 14 for history in histories.values()):
            raise HTTPException(
                422,
                detail={
                    "code": "INSUFFICIENT_HISTORY",
                    "message": "Cần ít nhất 14 kỳ lịch sử hoàn chỉnh để dự báo bằng Chronos-2.",
                },
            )
        if not self.model_path or not self.revision:
            raise ValueError("Provision a licensed local artifact and immutable revision")
        model_path = Path(self.model_path).resolve()
        if not model_path.is_dir() or not re.fullmatch(r"[0-9a-f]{40}", self.revision):
            raise ValueError("A local model directory and pinned commit are required")
        import pandas as pd
        import torch
        from chronos import BaseChronosPipeline

        with self.lock:
            if self.pipeline is None:
                manifest_bytes = (model_path / "model_provenance.json").read_bytes()
                manifest = json.loads(manifest_bytes)
                if (
                    manifest.get("repository") != "amazon/chronos-2"
                    or manifest.get("revision") != self.revision
                    or manifest.get("weights_license") != "Apache-2.0"
                    or not manifest.get("sha256")
                ):
                    raise ValueError("Licensed artifact manifest required")
                required_files = {
                    "config.json",
                    "LICENSE",
                    "README.md",
                    *(file.name for file in model_path.glob("*.safetensors")),
                }
                if (
                    len(required_files) < 4
                    or not required_files <= manifest["sha256"].keys()
                    or "Apache License" not in (model_path / "LICENSE").read_text()
                    or not re.search(
                        r"(?m)^license:\s*apache-2\.0\s*$",
                        (model_path / "README.md").read_text(encoding="utf-8"),
                    )
                ):
                    raise ValueError("Manifest must cover configuration, license and weights")
                for name, expected_hash in manifest["sha256"].items():
                    file = (model_path / name).resolve()
                    if not file.is_relative_to(model_path):
                        raise ValueError("Invalid artifact path")
                    with file.open("rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_hash:
                            raise ValueError("Artifact checksum mismatch")
                self.artifact_digest = hashlib.sha256(manifest_bytes).hexdigest()
                # Bound CPU contention with the API and other local engines.
                torch.set_num_threads(min(4, torch.get_num_threads()))
                self.pipeline = BaseChronosPipeline.from_pretrained(
                    str(model_path),
                    device_map="cpu",
                    local_files_only=True,
                )
                if type(self.pipeline).__name__ != "Chronos2Pipeline":
                    self.pipeline = None
                    raise ValueError("This provider accepts Chronos-2 only")
            required = {
                "prediction_length",
                "quantile_levels",
                "id_column",
                "timestamp_column",
                "target",
            }
            if not required <= set(inspect.signature(self.pipeline.predict_df).parameters):
                raise ValueError("Installed Chronos predict_df signature is incompatible")
            history_ends = {h[-1]["timestamp"] for h in histories.values()}
            if len(history_ends) != 1:
                raise ValueError("Series must share the configured historical end period")
            history_end = date.fromisoformat(history_ends.pop())
            origin = config.forecast_start or next_bucket(history_end, config.frequency)
            cursor = next_bucket(history_end, config.frequency)
            gap = 0
            while cursor < origin:
                gap += 1
                cursor = next_bucket(cursor, config.frequency)
            if cursor != origin:
                raise ValueError("Forecast origin must align with frequency")
            horizon = config.forecast_horizon

            def predict(history, start, length):
                frame = pd.DataFrame(history)
                frame["timestamp"] = pd.to_datetime(frame["timestamp"])
                with torch.inference_mode():
                    result = self.pipeline.predict_df(
                        frame,
                        prediction_length=length,
                        quantile_levels=[0.1, 0.5, 0.9],
                        id_column="item_id",
                        timestamp_column="timestamp",
                        target="target",
                    )
                lookup = {}
                nonnegative = min(p["target"] for p in history) >= 0
                for row in result.to_dict("records"):
                    stamp = pd.Timestamp(row["timestamp"]).date().isoformat()
                    key = (str(row["item_id"]), stamp)
                    values = [float(row[q]) for q in ("0.1", "0.5", "0.9")]
                    if key in lookup or not all(isfinite(v) for v in values):
                        raise ValueError("Duplicate or nonfinite model output")
                    if values != sorted(values):
                        raise ValueError("Unordered model quantiles")
                    if nonnegative:
                        values = [max(0, v) for v in values]
                    lookup[key] = {
                        "item_id": key[0],
                        "timestamp": stamp,
                        **dict(zip(("p10", "p50", "p90"), values, strict=True)),
                    }
                expected = []
                for entity in sorted({p["item_id"] for p in history}):
                    day = start
                    for _ in range(length):
                        expected.append((entity, day.isoformat()))
                        day = next_bucket(day, config.frequency)
                if set(lookup) != set(expected):
                    raise ValueError("Missing, extra or misaligned model output")
                return [lookup[key] for key in expected]

            # The pretrained weights stay fixed. Historical blocks only evaluate
            # zero-shot forecasts, never fit or fine-tune the model.
            full_start = next_bucket(history_end, config.frequency)
            full = predict(dataset["points"], full_start, horizon + gap)
            ordered = [p for p in full if p["timestamp"] >= origin.isoformat()]
            decisions, evaluation = {}, {}
            # Batch independent series with matching calendar boundaries. This
            # reuses three inference calls rather than three calls for every SKU.
            eligible = {e: h for e, h in histories.items() if len(h) >= 14 + 2 * horizon + gap}
            for block in (2, 1):
                contexts, actual_by_entity = [], {}
                starts = set()
                for entity, history in eligible.items():
                    end = len(history) - (block - 1) * horizon
                    cut = end - horizon - gap
                    context, actual = history[:cut], history[end - horizon : end]
                    contexts.extend(context)
                    actual_by_entity[entity] = (context, actual)
                    starts.add(
                        next_bucket(date.fromisoformat(context[-1]["timestamp"]), config.frequency)
                    )
                if contexts:
                    if len(starts) != 1:
                        raise ValueError("Series must share the configured historical end period")
                    predictions = predict(contexts, starts.pop(), horizon + gap)
                    for entity, (context, actual) in actual_by_entity.items():
                        predicted = [p for p in predictions if p["item_id"] == entity][gap:]
                        evaluation.setdefault(entity, []).append((context, actual, predicted))
            for entity, history in histories.items():
                errors, actuals, windows = [], [], []
                for context, actual, predicted in evaluation.get(entity, []):
                    errors.extend(
                        abs(p["p50"] - a["target"]) for p, a in zip(predicted, actual, strict=True)
                    )
                    actuals.extend(abs(a["target"]) for a in actual)
                    windows.append(
                        {
                            "training_end": context[-1]["timestamp"],
                            "validation_start": actual[0]["timestamp"],
                            "validation_end": actual[-1]["timestamp"],
                        }
                    )
                score = (
                    {
                        "mae": mean(errors),
                        "wape_percent": 100 * sum(errors) / sum(actuals) if sum(actuals) else None,
                        "windows": windows,
                    }
                    if errors
                    else None
                )
                decisions[entity] = {
                    "method": "chronos2",
                    "kind": "machine_learning",
                    "reason": "Pretrained Chronos-2 zero-shot inference. No local training.",
                    "observations": len(history),
                    "gap_days": gap,
                    "validation": {"chronos2": score} if score else {},
                    "context_input_sha256": hashlib.sha256(
                        json.dumps(history, sort_keys=True).encode()
                    ).hexdigest(),
                }
        return ordered, {
            "method": "chronos2",
            "kind": "machine_learning",
            "model": "amazon/chronos-2",
            "revision": self.revision,
            "code_version": version("chronos-forecasting"),
            "weights_license": "Apache-2.0",
            "artifact_manifest_sha256": self.artifact_digest,
            "inference": "local CPU",
            "pretrained": True,
            "fine_tuning": False,
            "entities": decisions,
            "forecast_start": origin.isoformat(),
            "last_observation": max(p["timestamp"] for p in dataset["points"]),
            "interval": "Chronos-2 pretrained quantiles. Coverage not calibrated on this business.",
            "nonnegative_policy": "Clip quantiles at zero only for nonnegative input series",
        }
