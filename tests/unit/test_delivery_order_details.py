# SPDX-License-Identifier: GPL-3.0-or-later
import json
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from flowbridge.supplier_assistant.delivery_statistics import (
    DeliveryOrdersRequest,
    StatisticsRequest,
    classify_orders,
    order_page,
)


def test_exact_order_filter_does_not_return_other_supplier_orders_or_scan_pages():
    details = []
    classify_orders(*example(), at(30), details=details)
    selected = details[0]["purchase_order_id"]
    rows = [deepcopy(details[0]) for _ in range(600)]
    for n, row in enumerate(rows):
        row.update(purchase_order_id=uuid4(), po_number=f"PO-{n}")
    rows.append(details[0])
    page = order_page(
        rows,
        DeliveryOrdersRequest(
            supplier_id=uuid4(),
            purchase_order_id=selected,
            delivery_filter="all",
            page_size=1,
        ),
    )
    assert page.purchase_order_id == selected and page.total_matches == 1
    assert page.items[0].purchase_order_id == selected and not page.has_more
    wrong = page.model_dump(mode="json")
    wrong["items"][0]["purchase_order_id"] = str(uuid4())
    with pytest.raises(ValidationError, match="DELIVERY_ORDER_SELECTION_MISMATCH"):
        type(page).model_validate_json(json.dumps(wrong))


def at(day):
    return datetime(2026, 9, day, 10, tzinfo=UTC)


def example():
    po, line, schedule = uuid4(), uuid4(), uuid4()
    orders = [
        {
            "purchase_order_id": po,
            "po_number": "DH-2026-0212",
            "order_date": at(1),
            "status": "issued",
        }
    ]
    items = [
        {
            "purchase_order_id": po,
            "po_item_id": line,
            "ordered_quantity": Decimal(120),
            "product_name": "Băng keo Scotch 371",
            "unit": "cuộn",
        }
    ]
    plans = [
        {
            "po_item_id": line,
            "schedule_id": schedule,
            "scheduled_quantity": Decimal(120),
            "created_at": at(1),
            "requested_delivery_date": date(2026, 9, 20),
        }
    ]
    receipts = [
        {
            "schedule_id": schedule,
            "receipt_id": uuid4(),
            "received_quantity": Decimal(q),
            "received_at": at(day),
            "actual_delivery_date": date(2026, 9, day),
        }
        for day, q in [(19, 100), (23, 20)]
    ]
    return orders, items, plans, receipts


def test_specific_order_full_receipt_and_original_deadline_are_auditable():
    details = []
    counts = classify_orders(*example(), at(30), details=details)
    page = order_page(details, StatisticsRequest(supplier_id=uuid4(), include_orders=True))
    row = page.items[0]
    assert counts.completed_late == 1
    assert row.po_number == "DH-2026-0212"
    assert row.max_days_late == 3
    assert row.schedules[0].product_name == "Băng keo Scotch 371"
    assert row.schedules[0].requested_delivery_date == date(2026, 9, 20)
    assert row.schedules[0].completion_date == date(2026, 9, 23)
    assert row.schedules[0].received_quantity == "120"
    assert len(row.schedules[0].receipt_ids) == 2


def test_open_overdue_has_no_invented_completion_and_does_not_use_future_receipts():
    details = []
    counts = classify_orders(*example(), at(21), details=details)
    page = order_page(details, StatisticsRequest(supplier_id=uuid4(), include_orders=True))
    row = page.items[0]
    assert counts.open_overdue == 1
    assert row.delivery_status == "open_overdue" and row.max_days_late == 1
    assert row.schedules[0].completion_date is None
    assert row.schedules[0].received_quantity == "100"
    assert len(row.schedules[0].receipt_ids) == 1


def test_all_late_orders_include_completed_late_schedule_in_not_due_open_order():
    orders, items, plans, receipts = example()
    items[0]["ordered_quantity"] = Decimal(140)
    plans.append(
        {
            **plans[0],
            "schedule_id": uuid4(),
            "scheduled_quantity": Decimal(20),
            "requested_delivery_date": date(2026, 9, 30),
        }
    )
    details = []
    counts = classify_orders(orders, items, plans, receipts, at(25), details=details)
    assert counts.open_not_due == 1
    assert order_page(details, StatisticsRequest(supplier_id=uuid4())).total_matches == 1
    assert (
        order_page(
            details, StatisticsRequest(supplier_id=uuid4(), delivery_filter="completed_late")
        ).total_matches
        == 0
    )


def test_pages_have_deterministic_order_no_duplicates_and_correct_total():
    details = []
    classify_orders(*example(), at(30), details=details)
    rows = []
    for index in range(13):
        row = deepcopy(details[0])
        row.update(purchase_order_id=uuid4(), po_number=f"DH-{index:03}")
        rows.append(row)
    pages = [order_page(rows, StatisticsRequest(supplier_id=uuid4(), page=i)) for i in (1, 2, 3)]
    assert [len(p.items) for p in pages] == [5, 5, 3]
    assert [p.next_page for p in pages] == [2, 3, None]
    assert len({r.purchase_order_id for p in pages for r in p.items}) == 13
    assert all(p.total_matches == 13 for p in pages)


def test_unknown_schedule_and_cancelled_orders_do_not_become_confirmed_late_orders():
    for unknown, cancelled in [(True, False), (False, True)]:
        orders, items, plans, receipts = example()
        if unknown:
            plans[0]["scheduled_quantity"] = Decimal(100)
        if cancelled:
            orders[0]["status"] = "cancelled"
        details = []
        classify_orders(orders, items, plans, receipts, at(30), details=details)
        assert order_page(details, StatisticsRequest(supplier_id=uuid4())).total_matches == 0


def test_request_bounds_pages_and_rejects_unsupported_filters():
    for extra in [
        {"page_size": 11},
        {"delivery_filter": "random"},
        {"page": 0},
        {"department_id": str(uuid4())},
    ]:
        with pytest.raises(ValidationError):
            StatisticsRequest(supplier_id=uuid4(), **extra)
