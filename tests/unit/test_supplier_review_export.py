# SPDX-License-Identifier: GPL-3.0-or-later
"""Review facts use completion, receipt knowledge time and local calendar dates."""

import runpy
from datetime import date, datetime
from pathlib import Path

MODULE = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/export_supplier_reviews.py")
)


def test_partial_receipt_is_not_completion_and_open_orders_are_not_ontime():
    fact = MODULE["po_facts"](
        {
            "ordered_quantity": 120,
            "requested_delivery_date": date(2026, 10, 10),
            "receipts": [
                {
                    "received_quantity": 100,
                    "received_at": datetime.fromisoformat("2026-10-09T10:00:00+07:00"),
                    "actual_delivery_date": date(2026, 10, 9),
                }
            ],
        }
    )
    assert fact["received"] == 100
    assert fact["completed_at"] is None and fact["late_days"] is None
    assert fact["overdue_days"] == 5


def test_delayed_recording_does_not_move_actual_completion_to_an_earlier_delivery():
    fact = MODULE["po_facts"](
        {
            "ordered_quantity": 120,
            "requested_delivery_date": date(2026, 9, 20),
            "receipts": [
                {
                    "received_quantity": 100,
                    "received_at": datetime.fromisoformat("2026-09-24T10:00:00+07:00"),
                    "actual_delivery_date": date(2026, 9, 19),
                },
                {
                    "received_quantity": 20,
                    "received_at": datetime.fromisoformat("2026-09-23T10:00:00+07:00"),
                    "actual_delivery_date": date(2026, 9, 23),
                },
            ],
        }
    )
    assert fact["completed_at"] == date(2026, 9, 23)
    assert fact["late_days"] == 3


def test_report_renders_utc_events_on_vietnam_calendar_day():
    assert MODULE["day"](datetime.fromisoformat("2026-09-26T20:33:00+00:00")) == "27/09/2026"
