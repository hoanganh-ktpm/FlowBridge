# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.supplier_assistant.delivery_statistics import (
    DeliveryStatistics,
    classify_orders,
    late_percent,
)


def at(day):
    return datetime(2026, 9, day, 10, tzinfo=UTC)


def facts(deadline=20, target="120", status="issued"):
    return (
        [{"purchase_order_id": "po", "order_date": at(1), "status": status}],
        [{"purchase_order_id": "po", "po_item_id": "item", "ordered_quantity": Decimal(target)}],
        [
            {
                "schedule_id": "s",
                "po_item_id": "item",
                "created_at": at(1),
                "scheduled_quantity": Decimal(target),
                "requested_delivery_date": date(2026, 9, deadline),
            }
        ],
    )


def receipt(day, quantity, *, known=None, schedule="s"):
    return {
        "receipt_id": f"receipt-{day}-{quantity}",
        "schedule_id": schedule,
        "received_at": known or at(day),
        "actual_delivery_date": date(2026, 9, day),
        "received_quantity": Decimal(quantity),
    }


def sample_statistics():
    return DeliveryStatistics(
        supplier_id=UUID("00000000-0000-4000-8000-000000000003"),
        supplier_code="NCC-03",
        supplier_name="Nhà cung cấp 03",
        as_of=at(30),
        issued_from=date(2026, 9, 1),
        issued_to=date(2026, 9, 30),
        total_eligible_orders=188,
        completed_orders=181,
        late_rate_percent=60.77,
        counts={
            "completed_on_time": 71,
            "completed_late": 110,
            "open_overdue": 4,
            "open_not_due": 3,
            "unknown_schedule": 0,
            "partial_open": 3,
            "excluded_cancelled": 2,
        },
        metric_definition="Mỗi lịch giao phải nhận đủ theo hạn gốc.",
        denominator_definition="Đơn nhận đủ trễ / tổng đơn đã nhận đủ.",
        complete=True,
        warnings=[],
    )


def test_split_delivery_uses_full_receipt_date_and_partial_is_not_a_separate_group():
    orders, items, plans = facts()
    receipts = [receipt(19, "100"), receipt(23, "20")]
    before = classify_orders(orders, items, plans, receipts, at(21))
    assert before.open_overdue == before.partial_open == 1
    assert before.completed_late == before.completed_on_time == 0
    after = classify_orders(orders, items, plans, receipts, at(30))
    assert after.completed_late == 1 and after.partial_open == after.open_overdue == 0


@pytest.mark.parametrize(
    "receipt_day,known,asof,field",
    [
        (19, at(19), at(30), "completed_on_time"),
        (23, at(23), at(30), "completed_late"),
        (19, at(25), at(21), "open_overdue"),  # Backdated receipt not known at cutoff.
        (25, at(19), at(21), "open_overdue"),  # Future physical delivery not counted.
    ],
)
def test_recording_and_delivery_dates_both_obey_cutoff(receipt_day, known, asof, field):
    counts = classify_orders(*facts(), [receipt(receipt_day, "120", known=known)], asof)
    assert getattr(counts, field) == 1


def test_multiple_schedules_cannot_hide_early_lateness_under_last_deadline():
    orders, items, plans = facts(target="120")
    plans[0].update(scheduled_quantity=Decimal("100"), requested_delivery_date=date(2026, 9, 10))
    plans.append(
        {
            **plans[0],
            "schedule_id": "s2",
            "scheduled_quantity": Decimal("20"),
            "requested_delivery_date": date(2026, 9, 30),
        }
    )
    counts = classify_orders(
        orders,
        items,
        plans,
        [
            receipt(12, "100"),
            receipt(20, "20", schedule="s2"),
        ],
        at(30),
    )
    assert counts.completed_late == 1 and counts.completed_on_time == 0


def test_open_not_due_and_missing_schedule_are_kept_separate_from_complete_orders():
    orders, items, plans = facts(deadline=30)
    counts = classify_orders(orders, items, plans, [receipt(19, "100")], at(21))
    assert counts.open_not_due == counts.partial_open == 1
    assert counts.completed_late == 0
    counts = classify_orders(orders, items, [], [], at(21))
    assert counts.unknown_schedule == 1 and counts.open_overdue == 0
    plans[0]["scheduled_quantity"] = Decimal("100")
    assert classify_orders(orders, items, plans, [], at(21)).unknown_schedule == 1


@pytest.mark.parametrize("status", ["cancelled", "rejected"])
def test_current_cancelled_status_is_excluded_explicitly(status):
    counts = classify_orders(*facts(status=status), [receipt(23, "120")], at(30))
    assert counts.excluded_cancelled == 1
    assert counts.completed_late == counts.open_overdue == 0


def test_zero_denominator_is_null_and_percent_uses_completed_orders_only():
    assert late_percent(0, 0) is None
    assert late_percent(110, 181) == 60.77
    sample = sample_statistics()
    assert sample.completed_orders == 181 and sample.total_eligible_orders == 188
    wrong = sample.model_dump()
    wrong["late_rate_percent"] = 58.51  # Dividing by all 188 would be incorrect.
    with pytest.raises(ValidationError, match="INVALID_LATE_RATE"):
        DeliveryStatistics.model_validate(wrong)
    wrong = deepcopy(sample.model_dump())
    wrong["counts"]["open_overdue"] += 3  # Partial orders must not be counted twice.
    with pytest.raises(ValidationError, match="OVERLAPPING_ORDER_COUNTS"):
        DeliveryStatistics.model_validate(wrong)


def test_statistics_openapi_is_available_without_enabling_ml():
    app = create_app(
        Settings(
            _env_file=None,
            ml_enabled=False,
            delivery_statistics_enabled=True,
            database_url="postgresql+asyncpg://x:y@localhost/test",
        )
    )
    operation = app.openapi()["paths"]["/internal/v1/tools/supplier-delivery-statistics"]["post"]
    assert operation["operationId"] == "getSupplierDeliveryStatistics"
    assert operation["security"]
    assert any(p["name"] == "X-Job-Context" for p in operation["parameters"])


def test_statistics_is_opt_in_and_requires_a_database():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, delivery_statistics_enabled=True)
    app = create_app(
        Settings(_env_file=None, database_url="postgresql+asyncpg://x:y@localhost/test")
    )
    assert "/internal/v1/tools/supplier-delivery-statistics" not in app.openapi()["paths"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["supplier-delivery-statistics", "supplier-delivery-orders"])
async def test_statistics_endpoint_rejects_unauthenticated_requests_before_database(path):
    app = create_app(
        Settings(
            _env_file=None,
            ml_enabled=False,
            delivery_statistics_enabled=True,
            database_url="postgresql+asyncpg://x:y@localhost/test",
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/internal/v1/tools/" + path,
            json={
                "supplier_id": str(sample_statistics().supplier_id),
            },
            headers={"X-Job-Context": "00000000-0000-4000-8000-000000000001"},
        )
    assert r.status_code == 401
