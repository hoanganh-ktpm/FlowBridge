# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.prediction import model
from flowbridge.supplier_assistant.prediction.schemas import Features, FeatureSnapshot


class Probabilities:
    def __init__(self, rows):
        self.rows = rows
        self.shape = (len(rows), len(rows[0]))

    def __iter__(self):
        return iter(self.rows)

    def __getitem__(self, index):
        return [row[index[1]] for row in self.rows]


def snapshot():
    now = datetime.now(UTC)
    return FeatureSnapshot(
        x=Features(
            prediction_stage="PR_CREATED",
            supplier_id=str(uuid4()),
            product_category="laptop",
            order_size_ratio=1,
            days_to_deadline=10,
            hist_sample_count=0,
            other_open_order_count=0,
        ),
        feature_asof=now,
        product_id=uuid4(),
        quantity="100",
        unit="piece",
        requested_delivery_date=now.date(),
    )


def runtime(rows, *, classes=(1, 0)):
    result = model.TabICLRuntime(Settings(_env_file=None))
    result.failure = None
    result.context = SimpleNamespace(cutoff=datetime.now(UTC) - timedelta(days=1))
    result.classifier = SimpleNamespace(
        classes_=list(classes), predict_proba=lambda _: Probabilities(rows)
    )
    return result


def test_adapter_selects_late_class_by_classes_not_fixed_column(monkeypatch):
    monkeypatch.setattr(model, "frame", lambda rows: rows)
    assert runtime([[0.8, 0.2]]).infer([snapshot()]) == [0.8]
    assert runtime([[0.8, 0.2]], classes=(0, 1)).infer([snapshot()]) == [0.2]


@pytest.mark.parametrize(
    "rows", [[[float("nan"), 0]], [[1.2, -0.2]], [[0.2, 0.2]], [[0.5, 0.3, 0.2]]]
)
def test_invalid_model_outputs_never_become_success(monkeypatch, rows):
    monkeypatch.setattr(model, "frame", lambda rows: rows)
    with pytest.raises(CoreError) as error:
        runtime(rows).infer([snapshot()])
    assert error.value.code == ErrorCode.MODEL_UNAVAILABLE


def test_context_label_cutoff_must_precede_the_prediction(monkeypatch):
    monkeypatch.setattr(model, "frame", lambda rows: rows)
    engine = runtime([[0.8, 0.2]])
    engine.context.cutoff = datetime.now(UTC) + timedelta(days=1)
    with pytest.raises(CoreError) as error:
        engine.infer([snapshot()])
    assert error.value.code == ErrorCode.MODEL_UNAVAILABLE


@pytest.mark.asyncio
async def test_model_queue_has_a_bounded_wait():
    engine = model.TabICLRuntime(Settings(_env_file=None, ml_queue_timeout_seconds=0.01))
    await engine.gate.acquire()
    try:
        with pytest.raises(CoreError) as error:
            await engine.predict([snapshot()])
        assert error.value.code == ErrorCode.RATE_LIMITED
    finally:
        engine.gate.release()
