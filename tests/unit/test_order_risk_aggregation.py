# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from flowbridge.supplier_assistant.prediction.features import OrderFact
from flowbridge.supplier_assistant.prediction.schemas import ItemRisk, OrderRisk, RiskResult
from flowbridge.supplier_assistant.prediction.service import aggregate_order, item_exposure


def item(score, value, currency="VND"):
    return ItemRisk(
        po_item_id=uuid4(),
        product_id=uuid4(),
        requested_delivery_date=date(2026, 10, 30),
        result=RiskResult(status="success", risk_score=score, risk_threshold=0.7),
        remaining_value=value,
        currency=currency,
    )


def test_small_expensive_risky_line_is_weighted_by_value_and_keeps_its_guard():
    rows = [item(0.9, "900"), item(0.1, "100")]
    result = aggregate_order(uuid4(), rows)
    assert result.representative_risk_score == pytest.approx(0.82)
    assert result.highest_item_risk_score == 0.9
    assert result.highest_risk_item_id == rows[0].po_item_id
    assert result.high_risk_item_count == 1
    assert result.aggregation_policy == "remaining_value_weighted_v2"
    assert not result.score_is_calibrated


def test_splitting_equivalent_exposure_does_not_change_weighted_score():
    base = aggregate_order(uuid4(), [item(0.9, "900"), item(0.1, "100")])
    split = aggregate_order(uuid4(), [item(0.9, "300"), item(0.9, "600"), item(0.1, "100")])
    assert base.representative_risk_score == split.representative_risk_score


@pytest.mark.parametrize(
    "rows",
    [
        [item(0.9, None), item(0.1, "100")],
        [item(0.9, "100", "USD"), item(0.1, "100", "VND")],
        [item(0.9, "0"), item(0.1, "0")],
    ],
)
def test_unknown_zero_or_mixed_currency_weights_never_fall_back_to_max(rows):
    result = aggregate_order(uuid4(), rows)
    assert result.representative_risk_score is None
    assert result.highest_item_risk_score == 0.9
    assert result.status == "partial"
    assert "ORDER_VALUE_WEIGHTS_UNAVAILABLE" in result.warnings


def test_exposure_uses_only_receipts_known_and_delivered_at_cutoff():
    now = datetime(2026, 10, 19, 3, tzinfo=UTC)
    fact = OrderFact(
        item={"ordered_quantity": Decimal(120), "unit_price": Decimal(20)},
        order={"currency": "VND"},
        receipts=[
            {
                "received_quantity": Decimal(100),
                "received_at": now,
                "actual_delivery_date": now.date(),
            },
            {
                "received_quantity": Decimal(20),
                "received_at": now + timedelta(days=1),
                "actual_delivery_date": now.date(),
            },
        ],
    )
    assert item_exposure(fact, now) == {"remaining_value": Decimal(400), "currency": "VND"}


def test_legacy_idempotent_payload_preserves_its_original_policy():
    original = {
        "purchase_order_id": str(uuid4()),
        "aggregation_policy": "max_open_item_v1",
        "representative_risk_score": 0.8,
        "status": "success",
        "is_complete": True,
        "items": [],
    }
    assert OrderRisk.model_validate(original).aggregation_policy == "max_open_item_v1"
