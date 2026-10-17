# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import asyncio
import hashlib
import json
import math
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode, ErrorDetail

from .schemas import (
    CATEGORICAL,
    FEATURE_NAMES,
    FEATURE_VERSION,
    PREPROCESSING_VERSION,
    Features,
    FeatureSnapshot,
)


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("A timezone is required")
    return result


class ContextDataset:
    # JSON only: never unpickle a fitted estimator supplied by a client.
    def __init__(self, path: Path, max_rows: int) -> None:
        if path.stat().st_size > 50_000_000:
            raise ValueError("Context artifact exceeds size limit")
        raw = path.read_bytes()
        manifest = json.loads(raw)
        if (
            manifest["feature_schema_version"] != FEATURE_VERSION
            or manifest["preprocessing_version"] != PREPROCESSING_VERSION
            or manifest["feature_names"] != list(FEATURE_NAMES)
            or manifest["source_kind"] not in {"observed", "synthetic"}
            or not manifest["provenance"].strip()
        ):
            raise ValueError("Invalid context manifest")
        self.version = str(manifest["context_dataset_version"])
        if not self.version or len(self.version) > 120:
            raise ValueError("Invalid context version")
        self.cutoff = timestamp(manifest["label_cutoff"])
        self.source_kind = manifest["source_kind"]
        self.quantity_references = manifest.get("quantity_references", {})
        for reference in self.quantity_references.values():
            scale = Decimal(str(reference["quantity"]))
            if not scale.is_finite() or scale <= 0 or not reference["unit"].strip():
                raise ValueError("Invalid quantity reference")
        self.sha256 = hashlib.sha256(raw).hexdigest()
        rows = manifest["rows"]
        if not 2 <= len(rows) <= max_rows:
            raise ValueError("Invalid context size")
        self.features, self.labels = [], []
        self.group_ids: set[str] = set()
        outcomes: dict[str, int] = {}
        for row in rows:
            if set(row["x"]) != set(FEATURE_NAMES):
                raise ValueError("Context must contain exactly 19 features")
            if type(row["y"]) is not int or row["y"] not in {0, 1}:
                raise ValueError("Invalid binary label")
            asof, known = timestamp(row["feature_asof"]), timestamp(row["label_known_at"])
            if not asof < known <= self.cutoff:
                raise ValueError("Context label availability leaks")
            group = str(row["group_id"])
            if not group or group in outcomes and outcomes[group] != row["y"]:
                raise ValueError("Inconsistent physical order label")
            outcomes[group] = row["y"]
            self.group_ids.add(group)
            self.features.append(Features.model_validate(row["x"]))
            self.labels.append(row["y"])
        if set(self.labels) != {0, 1}:
            raise ValueError("Context requires both classes")


def frame(features: list[Features]) -> Any:
    import numpy as np
    import pandas as pd

    result = pd.DataFrame([row.model_dump() for row in features], columns=FEATURE_NAMES)
    for name in FEATURE_NAMES:
        if name in CATEGORICAL:
            result[name] = result[name].astype("category")
        else:
            result[name] = pd.to_numeric(result[name], errors="raise").astype(float).fillna(np.nan)
    return result


class TabICLRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.context: ContextDataset | None = None
        self.classifier: Any = None
        self.checkpoint_sha256: str | None = None
        self.failure: str | None = "NOT_LOADED"
        self.lock = threading.Lock()
        self.gate = asyncio.Semaphore(1)

    @property
    def ready(self) -> bool:
        return self.classifier is not None and self.context is not None and self.failure is None

    def load(self) -> None:
        try:
            from tabicl import TabICLClassifier

            context = ContextDataset(
                self.settings.ml_context_path, self.settings.ml_max_context_rows
            )
            if context.cutoff > datetime.now(UTC):
                raise ValueError("Future context cutoff")
            checkpoint = self.settings.ml_checkpoint_path
            if not checkpoint.is_file():
                raise FileNotFoundError("Checkpoint is required")
            with checkpoint.open("rb") as stream:
                self.checkpoint_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            if self.settings.environment == "production" and context.source_kind != "observed":
                raise ValueError("Production requires an observed context")
            if (
                self.settings.ml_checkpoint_sha256 is not None
                and self.settings.ml_checkpoint_sha256 != self.checkpoint_sha256
            ):
                raise ValueError("Checkpoint hash mismatch")
            classifier = TabICLClassifier(
                model_path=str(checkpoint),
                allow_auto_download=False,
                checkpoint_version=self.settings.ml_model_version,
                device=self.settings.ml_device,
                n_estimators=self.settings.ml_n_estimators,
                n_jobs=self.settings.ml_cpu_threads,
                random_state=42,
                verbose=False,
                use_amp=False,
            )
            classifier.fit(frame(context.features), context.labels)
            if set(classifier.classes_) != {0, 1}:
                raise ValueError("Invalid model classes")
            # Exercise all-null categorical A columns at startup, not just fit().
            probe = next(
                (r for r in context.features if r.prediction_stage == "PR_CREATED"),
                context.features[0],
            )
            probabilities = classifier.predict_proba(frame([probe]))
            self.check_probabilities(probabilities, 1)
            self.context, self.classifier, self.failure = context, classifier, None
        except ImportError:
            self.failure = "ML_DEPENDENCIES_MISSING"
        except FileNotFoundError:
            self.failure = "ML_ARTIFACT_MISSING"
        except Exception:
            self.failure = "ML_ARTIFACT_OR_RUNTIME_INVALID"

    @staticmethod
    def check_probabilities(value: Any, count: int) -> None:
        if getattr(value, "shape", None) != (count, 2):
            raise ValueError("Invalid probability shape")
        for row in value:
            if any(not math.isfinite(float(x)) or not 0 <= float(x) <= 1 for x in row):
                raise ValueError("Invalid model output")
            if not math.isclose(float(sum(row)), 1, abs_tol=1e-4):
                raise ValueError("Invalid probability sum")

    def ensure_ready(self, asof: datetime | None = None) -> None:
        if not self.ready or asof is not None and self.context.cutoff > asof:
            raise CoreError(
                ErrorCode.MODEL_UNAVAILABLE,
                details=(ErrorDetail(field="model", reason=self.failure or "CONTEXT_CUTOFF"),),
            )

    def infer(self, snapshots: list[FeatureSnapshot]) -> list[float]:
        # The lock stays held until native inference finishes, even after caller cancellation.
        with self.lock:
            for snapshot in snapshots:
                self.ensure_ready(snapshot.feature_asof)
            try:
                probabilities = self.classifier.predict_proba(frame([s.x for s in snapshots]))
                self.check_probabilities(probabilities, len(snapshots))
                late_column = list(self.classifier.classes_).index(1)
                return [float(v) for v in probabilities[:, late_column]]
            except Exception:
                raise CoreError(ErrorCode.MODEL_UNAVAILABLE) from None

    async def predict(self, snapshots: list[FeatureSnapshot]) -> list[float]:
        if not snapshots:
            return []
        try:
            await asyncio.wait_for(
                self.gate.acquire(), timeout=self.settings.ml_queue_timeout_seconds
            )
        except TimeoutError:
            raise CoreError(ErrorCode.RATE_LIMITED, retry_after=2) from None
        try:
            # No blocking sklearn/torch work on the event loop.
            return await run_in_threadpool(self.infer, snapshots)
        finally:
            self.gate.release()

    def metadata(self) -> dict:
        self.ensure_ready()
        return {
            "context_sha256": self.context.sha256,
            "checkpoint_sha256": self.checkpoint_sha256,
            "label_cutoff": self.context.cutoff.isoformat(),
            "source_kind": self.context.source_kind,
            "feature_policy": {
                "history_days": self.settings.ml_history_days,
                "receipt_coverage_confirmed": self.settings.ml_receipt_coverage_confirmed,
            },
        }
