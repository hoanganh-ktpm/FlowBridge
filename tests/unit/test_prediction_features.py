# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.prediction.cli import demo_context
from flowbridge.supplier_assistant.prediction.features import FeatureBuilder, OrderFact
from flowbridge.supplier_assistant.prediction.model import ContextDataset
from flowbridge.supplier_assistant.prediction.schemas import FEATURE_NAMES, Features, RankRequest

T = datetime(2026, 10, 16, 3, tzinfo=UTC)
PRODUCT, SUPPLIER = uuid4(), uuid4()
PRODUCT_ROW = {"product_id": PRODUCT, "product_category": "laptop", "unit": "piece"}


def fact(*, supplier=SUPPLIER, complete=False, deadline=None, product=PRODUCT) -> OrderFact:
    item_id, order_id, schedule_id = uuid4(), uuid4(), uuid4()
    return OrderFact(
        item={
            "po_item_id": item_id,
            "purchase_order_id": order_id,
            "product_id": product,
            "product_category_snapshot": "laptop",
            "ordered_quantity": Decimal(100),
            "unit": "piece",
            "item_status": "completed" if complete else "open",
            "request_item_id": uuid4(),
        },
        order={
            "purchase_order_id": order_id,
            "supplier_id": supplier,
            "request_id": uuid4(),
            "order_date": T - timedelta(days=10),
            "status": "delivered" if complete else "issued",
        },
        schedules=[
            {
                "schedule_id": schedule_id,
                "po_item_id": item_id,
                "scheduled_quantity": Decimal(100),
                "requested_delivery_date": deadline or (T + timedelta(days=3)).date(),
            }
        ],
        receipts=[
            {
                "schedule_id": schedule_id,
                "received_quantity": Decimal(100),
                "actual_delivery_date": (T - timedelta(days=5)).date(),
                "received_at": T - timedelta(days=4),
            }
        ]
        if complete
        else [],
    )


def build(facts, *, current=None, coverage=False):
    return FeatureBuilder(receipt_coverage_confirmed=coverage).build(
        product=PRODUCT_ROW,
        supplier_id=SUPPLIER,
        quantity=Decimal(100),
        unit="piece",
        deadline=(T + timedelta(days=3)).date(),
        asof=T,
        facts=facts,
        current=current,
    )


def test_scenario_uses_exact_contract_and_observed_history_only():
    late = fact(complete=True, deadline=(T - timedelta(days=6)).date())
    ontime = fact(supplier=uuid4(), complete=True)
    pending = fact()
    snapshot = build([late, ontime, pending])
    x = snapshot.x
    assert tuple(x.model_dump()) == FEATURE_NAMES
    assert len(FEATURE_NAMES) == 19
    assert x.hist_sample_count == 1
    assert x.hist_late_rate_smoothed == pytest.approx(7 / 13)
    assert x.hist_lead_p50_days == x.hist_lead_p90_days == 5
    assert x.other_open_order_count == 1
    assert x.order_size_ratio == 1
    assert x.expected_issue_wait_days is None
    assert all(x.model_dump()[name] is None for name in FEATURE_NAMES[12:])
    assert x.non_working_days_to_deadline is None


def test_monitor_uses_actual_receipt_date_and_knowledge_cutoff():
    current = fact()
    schedule = current.schedules[0]["schedule_id"]
    current.receipts.extend(
        [
            {
                "schedule_id": schedule,
                "received_quantity": Decimal(40),
                "actual_delivery_date": (T - timedelta(days=2)).date(),
                "received_at": T - timedelta(days=1),
            },
            {
                "schedule_id": schedule,
                "received_quantity": Decimal(60),
                "actual_delivery_date": (T - timedelta(days=1)).date(),
                "received_at": T + timedelta(days=1),
            },
        ]
    )
    snapshot = build([current, fact(complete=True)], current=current, coverage=True)
    assert snapshot.x.expected_issue_wait_days is None
    assert snapshot.x.days_since_last_receipt == 2
    assert snapshot.x.received_fraction == 0.4
    assert snapshot.x.remaining_size_ratio == 0.6
    assert snapshot.x.order_size_ratio == 1
    assert snapshot.x.elapsed_days_since_issue == 10
    assert snapshot.x.other_open_order_count == 0
    assert snapshot.x.confirmation_state == "waiting_confirmation"


def test_missing_reporting_coverage_is_not_zero_receipts():
    current = fact()
    x = build([current, fact(complete=True)], current=current).x
    assert x.received_fraction is None
    assert x.remaining_size_ratio is None
    assert x.days_since_last_receipt is None


def test_no_quantity_reference_fails_and_cold_start_can_use_reviewed_reference():
    with pytest.raises(CoreError) as error:
        build([])
    assert error.value.code == ErrorCode.INSUFFICIENT_DATA
    snapshot = FeatureBuilder(
        quantity_references={str(PRODUCT): {"unit": "piece", "quantity": "50"}}
    )
    x = snapshot.build(
        product=PRODUCT_ROW,
        supplier_id=SUPPLIER,
        quantity=Decimal(100),
        unit="piece",
        deadline=T.date(),
        asof=T,
        facts=[],
    ).x
    assert x.order_size_ratio == 2
    assert x.hist_sample_count == 0
    assert x.hist_late_rate_smoothed is None


def test_promise_requires_coverage_for_every_remaining_schedule():
    current = fact()
    current.schedules[0]["scheduled_quantity"] = Decimal(50)
    current.schedules.append({**current.schedules[0], "schedule_id": uuid4()})
    confirmation = uuid4()
    current.confirmations.append(
        {
            "confirmation_id": confirmation,
            "decision": "confirmed",
            "responded_at": T - timedelta(days=1),
        }
    )
    for index, schedule in enumerate(current.schedules):
        current.promises.append(
            {
                "schedule_id": schedule["schedule_id"],
                "confirmation_id": confirmation,
                "revision_no": 1,
                "recorded_at": T - timedelta(hours=1),
                "confirmed_quantity": Decimal(50),
                "promised_delivery_date": (T + timedelta(days=index + 1)).date(),
            }
        )
    assert FeatureBuilder.promise_margin(current, [], T, current.deadline()) == -1
    current.promises[1]["confirmed_quantity"] = Decimal(20)
    assert FeatureBuilder.promise_margin(current, [], T, current.deadline()) is None
    current.promises[1]["recorded_at"] = T + timedelta(days=1)
    assert FeatureBuilder.promise_margin(current, [], T, current.deadline()) is None


def test_label_is_first_accepted_completion_and_not_last_receipt():
    current = fact(complete=True)
    current.receipts.append(
        {
            **current.receipts[0],
            "actual_delivery_date": T.date(),
            "received_at": T,
            "received_quantity": Decimal(1),
        }
    )
    completion, known = current.outcome(T)
    assert completion == (T - timedelta(days=5)).date()
    assert known == T - timedelta(days=4)


def test_multiple_deadlines_require_different_grain():
    current = fact()
    current.schedules.append(
        {**current.schedules[0], "schedule_id": uuid4(), "requested_delivery_date": T.date()}
    )
    with pytest.raises(CoreError):
        current.deadline()


@pytest.mark.parametrize(
    "change",
    [
        {"prediction_stage": "PO_MONITORING", "expected_issue_wait_days": 0},
        {"received_fraction": 0.2},
        {"order_size_ratio": float("nan")},
        {"hist_lead_p50_days": 10, "hist_lead_p90_days": 2},
    ],
)
def test_invalid_feature_domains_and_stages_rejected(change):
    x = build([fact(complete=True)]).x.model_dump() | change
    with pytest.raises(ValidationError):
        Features.model_validate(x)


@pytest.mark.parametrize("quantity", [100, True, 1.2, "NaN", "0", "-1", "0.00001"])
def test_request_rejects_ambiguous_or_invalid_quantity(quantity):
    with pytest.raises(ValidationError):
        RankRequest(product_id=PRODUCT, quantity=quantity, requested_delivery_date=T.date())


@pytest.fixture
def context_file(tmp_path: Path) -> Path:
    path = tmp_path / "context.json"
    demo_context(path, 96)
    return path


def test_context_preserves_physical_groups_and_label_cutoff(context_file):
    context = ContextDataset(context_file, 500)
    assert set(context.labels) == {0, 1}
    assert len(context.features) == 192
    assert len(context.group_ids) == 96
    assert context.source_kind == "synthetic"
    assert len(context.sha256) == 64


@pytest.mark.parametrize(
    "issue",
    [
        "future_label",
        "missing_feature",
        "extra_feature",
        "inconsistent_label",
        "single_class",
        "naive_time",
    ],
)
def test_context_rejects_leakage_and_schema_mismatch(context_file, issue):
    manifest = json.loads(context_file.read_text())
    row = manifest["rows"][0]
    if issue == "future_label":
        row["label_known_at"] = "2099-01-01T00:00:00Z"
    elif issue == "missing_feature":
        del row["x"]["confirmation_state"]
    elif issue == "extra_feature":
        row["x"]["y"] = 1
    elif issue == "inconsistent_label":
        manifest["rows"][1]["y"] = 1 - row["y"]
    elif issue == "single_class":
        for entry in manifest["rows"]:
            entry["y"] = 0
    else:
        row["feature_asof"] = "2025-01-01T00:00:00"
    context_file.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, ValidationError)):
        ContextDataset(context_file, 500)
