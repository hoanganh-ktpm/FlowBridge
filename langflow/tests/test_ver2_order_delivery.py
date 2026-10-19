# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from flowbridge_agent.config import BackendConfig
from flowbridge_agent.ver2_data import DataBackend
from flowbridge_agent.ver2_order_delivery import order_delivery

ORDER = "00000001-0001-4001-8001-000000000001"
SUPPLIER = "00000002-0002-4002-8002-000000000002"
SCHEDULE = "00000003-0003-4003-8003-000000000003"
RECEIPT = "00000004-0004-4004-8004-000000000004"


def facts():
    order = {
        "purchase_order_id": ORDER,
        "po_number": "DEMO-PO-00656",
        "supplier_id": SUPPLIER,
        "status": "delivered",
        "items": [],
        "schedules": [
            {
                "schedule_id": SCHEDULE,
                "scheduled_quantity": "89",
                "requested_delivery_date": "2026-05-09",
                "promised_delivery_date": "2026-05-15",
            }
        ],
    }
    evidence = {
        "supplier_id": SUPPLIER,
        "supplier_code": "DEMO-S01",
        "supplier_name": "An Phú — NCC-01",
        "as_of": "2026-10-20T00:00:00Z",
        "issued_from": "2026-04-25",
        "issued_to": "2026-10-20",
        "scope": "company_procurement_reader",
        "total_eligible_orders": 1,
        "completed_orders": 1,
        "late_rate_percent": 100.0,
        "counts": {
            "completed_on_time": 0,
            "completed_late": 1,
            "open_overdue": 0,
            "open_not_due": 0,
            "unknown_schedule": 0,
            "partial_open": 0,
            "excluded_cancelled": 0,
        },
        "metric_definition": "Nhận đủ theo hạn gốc",
        "denominator_definition": "Đơn đã nhận đủ",
        "complete": True,
        "warnings": [],
        "orders": {
            "purchase_order_id": ORDER,
            "delivery_filter": "all",
            "total_matches": 1,
            "page": 1,
            "page_size": 1,
            "has_more": False,
            "next_page": None,
            "items": [
                {
                    "purchase_order_id": ORDER,
                    "po_number": "DEMO-PO-00656",
                    "delivery_status": "completed_late",
                    "max_days_late": 24,
                    "late_schedule_count": 1,
                    "details_truncated": False,
                    "schedules": [
                        {
                            "schedule_id": SCHEDULE,
                            "product_name": "Bàn phím Logitech K120",
                            "unit": "cái",
                            "scheduled_quantity": "89",
                            "received_quantity": "89",
                            "requested_delivery_date": "2026-05-09",
                            "completion_date": "2026-06-02",
                            "days_late": 24,
                            "receipt_ids": [RECEIPT],
                            "receipts_truncated": False,
                        }
                    ],
                }
            ],
        },
    }
    return order, evidence


@pytest.mark.asyncio
async def test_order_tool_returns_actual_receipt_and_supplier_in_one_lookup():
    order, evidence = facts()
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/api/v1/purchase-orders":
            return httpx.Response(200, json={"items": [order], "total": 1})
        if request.url.path.endswith("/" + ORDER):
            return httpx.Response(200, json=order)
        assert request.url.path == "/internal/v1/tools/supplier-delivery-orders"
        import json

        body = json.loads(request.content)
        assert body["purchase_order_id"] == ORDER and body["supplier_id"] == SUPPLIER
        assert body["delivery_filter"] == "all" and body["page_size"] == 1
        return httpx.Response(200, json=evidence)

    api = DataBackend(BackendConfig(user_token="test"), httpx.MockTransport(handle))
    result = await api.execute("purchase_get_order", {"identifier": "DEMO-PO-00656"})
    assert result["supplier_name"] == evidence["supplier_name"]
    assert result["delivery_evidence_status"] == "verified"
    plan = result["delivery_evidence"]["orders"]["items"][0]["schedules"][0]
    assert plan["completion_date"] == "2026-06-02" and plan["days_late"] == 24
    assert plan["receipt_ids"] == [RECEIPT]
    assert result["schedules"][0]["promised_delivery_date"] == "2026-05-15"
    assert len(calls) == 3  # No separate supplier catalog scan or model call.
    from flowbridge_agent.frontend_response_builder import build_response
    from flowbridge_agent.tools import tool_result

    frontend = build_response("Đã nhận đủ trễ", [tool_result("purchase_get_order", {}, result)])
    rows = frontend["datasets"][0]["rows"]
    assert rows[0]["completion_date"] == "2026-06-02" and rows[0]["days_late"] == 24
    assert rows[0]["supplier_name"] == evidence["supplier_name"]
    assert frontend["datasets"][0]["context"]["count_unit"] == "lịch giao"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["old_backend", "supplier", "order", "code", "deadline", "quantity", "scope"]
)
async def test_unmatched_or_stale_order_evidence_is_never_attached(change):
    order, evidence = facts()
    if change == "old_backend":
        evidence["orders"].pop("purchase_order_id")
    elif change == "supplier":
        evidence["supplier_id"] = RECEIPT
    elif change == "order":
        evidence["orders"]["items"][0]["purchase_order_id"] = RECEIPT
    elif change == "code":
        evidence["orders"]["items"][0]["po_number"] = "DEMO-PO-OTHER"
    elif change == "deadline":
        evidence["orders"]["items"][0]["schedules"][0]["requested_delivery_date"] = "2026-05-10"
    elif change == "quantity":
        evidence["orders"]["items"][0]["schedules"][0]["scheduled_quantity"] = "90"
    else:
        evidence["scope"] = "global_ai_service"
    client = SimpleNamespace(request=AsyncMock(return_value=evidence))
    result, error = await order_delivery(client, order)
    assert result is None and error == "DELIVERY_EVIDENCE_INVALID_OR_ORDER_CHANGED"


@pytest.mark.asyncio
async def test_no_receipts_remains_open_and_never_substitutes_promise_date():
    order, evidence = facts()
    evidence["completed_orders"] = 0
    evidence["late_rate_percent"] = None
    evidence["counts"].update(completed_late=0, open_overdue=1)
    row = evidence["orders"]["items"][0]
    row.update(delivery_status="open_overdue", max_days_late=164)
    row["schedules"][0].update(
        completion_date=None, received_quantity="0", receipt_ids=[], days_late=164
    )
    client = SimpleNamespace(request=AsyncMock(return_value=deepcopy(evidence)))
    result, error = await order_delivery(client, order)
    assert error is None
    assert result["orders"]["items"][0]["schedules"][0]["completion_date"] is None
