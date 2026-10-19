# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.prediction.features import FeatureBuilder, OrderFact
from flowbridge.supplier_assistant.prediction.history import HistoryReference, HistoryRequest
from flowbridge.supplier_assistant.prediction.service import PredictionService

ASOF = datetime(2026, 10, 19, 16, tzinfo=UTC)
PRODUCT, SUPPLIER = uuid4(), uuid4()
PRODUCT_ROW = {
    "product_id": PRODUCT,
    "product_category": "office",
    "unit": "cái",
    "product_name": "Chuột Logitech B100 USB",
}


def fact(index=0):
    order_id, item_id, schedule_id = uuid4(), uuid4(), uuid4()
    issued = ASOF - timedelta(days=30 + index)
    return OrderFact(
        item={
            "po_item_id": item_id,
            "purchase_order_id": order_id,
            "product_id": PRODUCT,
            "unit": "cái",
            "ordered_quantity": Decimal(100),
            "item_status": "completed",
            "product_category_snapshot": "office",
        },
        order={
            "purchase_order_id": order_id,
            "po_number": f"DH-{index:03}",
            "supplier_id": SUPPLIER,
            "order_date": issued,
            "status": "delivered",
        },
        schedules=[
            {
                "schedule_id": schedule_id,
                "requested_delivery_date": (issued + timedelta(days=5)).date(),
                "scheduled_quantity": Decimal(100),
            }
        ],
        receipts=[
            {
                "received_quantity": Decimal(100),
                "received_at": issued + timedelta(days=9),
                "actual_delivery_date": (issued + timedelta(days=8)).date(),
            }
        ],
    )


def snapshot(rows):
    return FeatureBuilder().build(
        product=PRODUCT_ROW,
        supplier_id=SUPPLIER,
        quantity=Decimal(9),
        unit="cái",
        deadline=(ASOF + timedelta(days=5)).date(),
        asof=ASOF,
        facts=rows,
    )


def service(rows):
    core = SimpleNamespace(
        get=AsyncMock(
            side_effect=lambda n, i: PRODUCT_ROW if n == "products" else {"supplier_id": SUPPLIER}
        )
    )
    repo = SimpleNamespace(core=core, facts=AsyncMock(return_value=rows))
    runtime = SimpleNamespace(
        context=SimpleNamespace(source_kind="observed"),
        predict=AsyncMock(side_effect=AssertionError("No inference")),
    )
    svc = PredictionService(repo, runtime, Settings(_env_file=None))
    svc.policy.grants = {"PROCUREMENT_VIEWER": [{}]}
    return svc


@pytest.mark.asyncio
async def test_eleven_feature_samples_are_exactly_the_eleven_visible_history_rows():
    rows = [fact(i) for i in range(11)]
    # Supplier, unit, category, unfinished and outside-window rows must not enter the cohort.
    for changes in (
        {"unit": "hộp"},
        {"product_id": uuid4()},
        {"item_status": "cancelled"},
        {"product_category_snapshot": "other"},
    ):
        extra = fact(20)
        extra.item.update(changes)
        rows.append(extra)
    other = fact(21)
    other.order["supplier_id"] = uuid4()
    rows.append(other)
    unknown_at_cutoff = fact(22)
    unknown_at_cutoff.receipts[0]["received_at"] = ASOF + timedelta(days=1)
    rows.append(unknown_at_cutoff)
    snap = snapshot(rows)
    ref = HistoryReference.model_validate(snap.metadata["history_reference"])
    assert snap.x.hist_sample_count == ref.sample_count == 11
    svc = service(rows)
    result = await svc.history(
        HistoryRequest(product_id=PRODUCT, supplier_id=SUPPLIER, reference=ref, expected_count=11)
    )
    assert result.reference_matches is True and result.expected_count_matches is True
    assert len(result.items) == result.total_matches == 11
    assert {r.po_item_id for r in result.items} == {r.item["po_item_id"] for r in rows[:11]}
    assert all(r.days_late == 3 and r.delivered_late for r in result.items)
    assert all(r.product_name == PRODUCT_ROW["product_name"] for r in result.items)
    svc.runtime.predict.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_result_without_reference_is_reconstructed_not_verified_even_if_count_matches():
    svc = service([fact(i) for i in range(11)])
    result = await svc.history(
        HistoryRequest(
            product_id=PRODUCT, supplier_id=SUPPLIER, feature_asof=ASOF, expected_count=11
        )
    )
    assert result.total_matches == 11 and result.expected_count_matches is True
    assert result.reference_matches is None
    assert "HISTORY_RECONSTRUCTED_WITHOUT_ORIGINAL_REFERENCE" in result.warnings
    assert "CURRENT_HISTORY_NOT_PREVIOUS_PREDICTION" not in result.warnings


@pytest.mark.asyncio
async def test_changed_membership_with_identical_count_is_detected():
    rows = [fact(i) for i in range(11)]
    ref = HistoryReference.model_validate(snapshot(rows).metadata["history_reference"])
    rows[-1] = fact(15)
    svc = service(rows)
    result = await svc.history(
        HistoryRequest(product_id=PRODUCT, supplier_id=SUPPLIER, reference=ref, expected_count=11)
    )
    assert result.expected_count_matches is True
    assert result.reference_matches is False and result.status == "changed"


@pytest.mark.asyncio
async def test_new_receipts_never_leak_into_old_cutoff_and_pagination_is_stable():
    rows = [fact(i) for i in range(11)]
    ref = HistoryReference.model_validate(snapshot(rows).metadata["history_reference"])
    future = fact(12)
    future.receipts[0]["received_at"] = ASOF + timedelta(seconds=1)
    svc = service([*reversed(rows), future])
    pages = [
        await svc.history(
            HistoryRequest(
                product_id=PRODUCT, supplier_id=SUPPLIER, reference=ref, page=p, page_size=5
            )
        )
        for p in (1, 2, 3)
    ]
    assert [len(p.items) for p in pages] == [5, 5, 1]
    assert [p.next_page for p in pages] == [2, 3, None]
    assert all(p.reference_matches is True for p in pages)
    assert len({r.po_item_id for p in pages for r in p.items}) == 11


@pytest.mark.asyncio
async def test_regular_employee_cannot_read_company_wide_feature_evidence():
    svc = service([fact()])
    svc.policy.grants = {"EMPLOYEE": [{}]}
    with pytest.raises(CoreError) as error:
        await svc.history(HistoryRequest(product_id=PRODUCT, supplier_id=SUPPLIER))
    assert error.value.code == ErrorCode.FORBIDDEN
    svc.repo.facts.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflicting_reference_identity_is_not_used_for_another_supplier():
    ref = HistoryReference.model_validate(snapshot([fact()]).metadata["history_reference"])
    svc = service([])
    with pytest.raises(CoreError):
        await svc.history(HistoryRequest(product_id=PRODUCT, supplier_id=uuid4(), reference=ref))
    svc.repo.facts.assert_not_awaited()


def test_timezone_required_and_model_result_includes_inspectable_reference():
    with pytest.raises(ValidationError):
        HistoryRequest(product_id=PRODUCT, supplier_id=SUPPLIER, feature_asof="2026-10-19T00:00:00")
    svc = service([])
    snap = snapshot([fact()])
    result = svc.result(0.78, snap)
    assert result.hist_sample_count == result.history_reference.sample_count == 1
    changed = deepcopy(snap.metadata["history_reference"])
    changed["feature_asof"] = "2026-10-19T00:00:00"
    with pytest.raises(ValidationError):
        HistoryReference.model_validate(changed)
