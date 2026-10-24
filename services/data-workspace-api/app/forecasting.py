# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import inspect
import json
import re
import threading
from collections import defaultdict
from datetime import date
from importlib.metadata import version
from pathlib import Path
from statistics import mean, quantiles

from fastapi import HTTPException

from .analytics import next_bucket


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

    def run(self, dataset, config, allow_fallback=True):
        if config.forecast_model in {"auto", "seasonal_mean", "ridge_lag"}:
            from .learning import validated_forecast

            return validated_forecast(dataset, config)
        try:
            points, model = self.chronos(dataset, config)
            return points, model
        except (ImportError, OSError, ValueError, RuntimeError, TypeError, KeyError):
            if not allow_fallback:
                raise HTTPException(
                    503, "Chronos-2 unavailable or incompatible; no inference claimed"
                ) from None
            points, model = statistical_forecast(dataset, config)
            model["fallback_reason"] = "Local pinned Chronos-2 unavailable or incompatible"
            model["requested_method"] = "chronos2"
            return points, model

    def chronos(self, dataset, config):
        import pandas as pd
        from chronos import BaseChronosPipeline

        if not self.model_path or not self.revision:
            raise ValueError("Provision a licensed local artifact and immutable revision")
        model_path = Path(self.model_path).resolve()
        if not model_path.is_dir() or not re.fullmatch(r"[0-9a-f]{40}", self.revision):
            raise ValueError("A local model directory and pinned commit are required")
        if any(
            sum(p["item_id"] == e for p in dataset["points"]) < 14
            for e in {p["item_id"] for p in dataset["points"]}
        ):
            raise ValueError("Insufficient context")
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
                    *(file.name for file in model_path.glob("*.safetensors")),
                }
                if (
                    len(required_files) < 3
                    or not required_files <= manifest["sha256"].keys()
                    or "Apache License" not in (model_path / "LICENSE").read_text()
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
            frame = pd.DataFrame(dataset["points"])
            frame["timestamp"] = pd.to_datetime(frame["timestamp"])
            expected, _ = statistical_forecast(dataset, config)
            origin = date.fromisoformat(expected[0]["timestamp"])
            cursor = next_bucket(config.end, config.frequency)
            gap = 0
            while cursor < origin:
                gap += 1
                cursor = next_bucket(cursor, config.frequency)
            if cursor != origin:
                raise ValueError("Forecast origin must align with frequency")
            result = self.pipeline.predict_df(
                frame,
                prediction_length=config.forecast_horizon + gap,
                quantile_levels=[0.1, 0.5, 0.9],
                id_column="item_id",
                timestamp_column="timestamp",
                target="target",
            )
            points = [
                {
                    "item_id": str(row["item_id"]),
                    "timestamp": pd.Timestamp(row["timestamp"]).date().isoformat(),
                    "p10": float(row["0.1"]),
                    "p50": float(row["0.5"]),
                    "p90": float(row["0.9"]),
                }
                for row in result.to_dict("records")
                if pd.Timestamp(row["timestamp"]).date() >= origin
            ]
        # Reject incomplete, nonfinite, reordered or misaligned provider outputs.
        import math

        expected, _ = statistical_forecast(dataset, config)
        lookup = {(p["item_id"], p["timestamp"]): p for p in points}
        if len(points) != len(expected) or len(lookup) != len(points):
            raise ValueError("Incomplete model output")
        ordered = []
        for point in expected:
            actual = lookup[(point["item_id"], point["timestamp"])]
            values = [actual[k] for k in ("p10", "p50", "p90")]
            if not all(math.isfinite(v) for v in values) or values != sorted(values):
                raise ValueError("Invalid quantiles")
            ordered.append(actual)
        return ordered, {
            "method": "chronos2",
            "kind": "ML",
            "model": "amazon/chronos-2",
            "revision": self.revision,
            "code_version": version("chronos-forecasting"),
            "weights_license": "Apache-2.0",
            "artifact_manifest_sha256": self.artifact_digest,
            "inference": "local CPU",
        }
