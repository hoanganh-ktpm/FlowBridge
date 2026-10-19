# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual tool adapters over offline HTTP, with no model/provider requests."""

import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.caller import CALLER, Caller  # noqa: E402
from flowbridge_agent.config import BackendConfig  # noqa: E402
from flowbridge_agent.frontend_response_builder import build_response  # noqa: E402
from flowbridge_agent.ver2_coordination import Request, invocation  # noqa: E402
from flowbridge_agent.ver2_data import DataBackend  # noqa: E402
from flowbridge_agent.ver2_delivery_orders import delivery_reply  # noqa: E402
from flowbridge_agent.ver2_protocol_nodes import A2AProtocolAdapter  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402

SUPPLIER = str(uuid4())


def statistics():
    return {
        "supplier_id": SUPPLIER,
        "supplier_code": "NCC-03",
        "supplier_name": "Vật tư Nam Việt",
        "scope": "caller_visible_orders",
        "as_of": "2026-10-19T03:00:00Z",
        "issued_from": "2026-09-01",
        "issued_to": "2026-10-19",
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
        "metric_definition": "So từng lịch giao với hạn gốc.",
        "denominator_definition": "Đơn nhận đủ trễ / tổng đơn đã nhận đủ.",
        "complete": True,
        "warnings": [],
        "orders": {
            "delivery_filter": "late",
            "page": 1,
            "page_size": 5,
            "has_more": False,
            "next_page": None,
            "total_matches": 1,
            "items": [
                {
                    "purchase_order_id": str(uuid4()),
                    "po_number": "DH-2026-0212",
                    "delivery_status": "completed_late",
                    "max_days_late": 3,
                    "late_schedule_count": 1,
                    "details_truncated": False,
                    "schedules": [
                        {
                            "schedule_id": str(uuid4()),
                            "product_name": "Băng keo Scotch 371",
                            "unit": "cuộn",
                            "scheduled_quantity": "120",
                            "received_quantity": "120",
                            "requested_delivery_date": "2026-09-20",
                            "completion_date": "2026-09-23",
                            "days_late": 3,
                            "receipt_ids": [str(uuid4())],
                        }
                    ],
                }
            ],
        },
    }


def request(**known):
    return Request(
        task="delivery_orders",
        question="Đơn nào của NCC-03 giao trễ?",
        business_date="2026-10-19",
        known={"supplier": "NCC-03", **known},
    )


def client(payload=None, code=200, suppliers=None):
    calls = []

    def handle(req):
        calls.append(req)
        if req.url.path == "/api/v1/suppliers":
            rows = (
                suppliers
                if suppliers is not None
                else [
                    {
                        "supplier_id": SUPPLIER,
                        "supplier_code": "NCC-03",
                        "supplier_name": "Vật tư Nam Việt",
                        "status": "active",
                    }
                ]
            )
            return httpx.Response(200, json={"items": rows, "total": len(rows)})
        return httpx.Response(code, json=payload if payload is not None else statistics())

    return DataBackend(
        BackendConfig(
            origin="http://test", user_token="human", ai_token="service", job_context=str(uuid4())
        ),
        httpx.MockTransport(handle),
    ), calls


@pytest.mark.asyncio
async def test_specific_orders_reach_a2a_and_frontend_without_child_model(
    monkeypatch,
):
    backend, calls = client()
    monkeypatch.setattr("flowbridge_agent.ver2_delivery_orders.DataBackend", lambda _: backend)
    original_handler = AsyncMock(side_effect=AssertionError("No child agent call expected"))

    async def original_call(input_value: str):
        return await original_handler(input_value)

    original = StructuredTool.from_function(
        coroutine=original_call,
        name="hoi_du_lieu",
        description="Data",
    )
    tool = A2AProtocolAdapter(
        source_tools=[original], caller_service="central", target_service="database"
    ).build_tools()[0]
    assert "delivery_orders" in tool.description and "không dùng RAG" in tool.description
    with invocation("central") as scope:
        result = await tool.ainvoke({"input_value": request().model_dump_json()})
        assert result["status"] == "success"
        assert result["resolved"]["supplier_id"] == SUPPLIER
        assert result["trace"]["path"] == ["central", "database"]
        assert scope.run.a2a_calls == 1 and scope.run.model_calls == 0
        assert len(scope.run.evidence) == 2
        output = build_response("Các đơn được tìm thấy", result["results"])
    original_handler.assert_not_awaited()
    row = output["datasets"][-1]["rows"][0]
    assert row["po_number"] == "DH-2026-0212" and row["days_late"] == 3
    assert row["completion_date"] == "2026-09-23"
    assert row["product_name"] == "Băng keo Scotch 371"
    assert output["datasets"][-1]["context"]["as_of"] == "2026-10-19T03:00:00Z"
    assert calls[0].headers["Authorization"] == "Bearer human"
    assert calls[1].headers["Authorization"] == "Bearer human"
    assert "X-Job-Context" not in calls[1].headers


@pytest.mark.asyncio
async def test_cache_and_budget_are_scoped_to_one_execution():
    backend, calls = client()
    with invocation("central") as scope:
        first = await delivery_reply(request(), scope, client=backend)
        scope.run.a2a_calls = scope.run.max_a2a
        assert await delivery_reply(request(), scope, client=backend) == first
        assert len(calls) == 2
        denied = await delivery_reply(request(page=2), scope, client=backend)
        assert denied["warnings"] == ["A2A_BUDGET_EXHAUSTED"]
        assert len(calls) == 2
    with invocation("central") as other:
        await delivery_reply(request(), other, client=client()[0])
        assert other.run.identifier != first["trace"]["request_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 403, 429, 503])
async def test_backend_errors_are_preserved_and_never_replaced_with_rag_numbers(code):
    backend, calls = client(code=code)
    with invocation("central") as scope:
        result = await delivery_reply(request(), scope, client=backend)
    assert result["status"] == "error"
    assert result["results"][-1]["error"]["http_status"] == code
    assert result["results"][-1]["data"] is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_unknown_supplier_does_not_fetch_other_supplier_orders():
    backend, calls = client(suppliers=[])
    with invocation("central") as scope:
        result = await delivery_reply(request(), scope, client=backend)
    assert result["status"] == "not_found" and len(calls) == 1


@pytest.mark.asyncio
async def test_unsupported_period_filter_is_not_silently_ignored():
    backend, calls = client()
    with invocation("central") as scope:
        result = await delivery_reply(request(issued_from="2026-10-01"), scope, client=backend)
    assert result["status"] == "needs_input" and calls == []


@pytest.mark.asyncio
async def test_mismatched_supplier_and_corrupt_page_are_rejected():
    for modify in (
        lambda d: d.update(supplier_id=str(uuid4())),
        lambda d: d["orders"].update(next_page=2),
        lambda d: d.update(scope="global_ai_service"),
    ):
        data = deepcopy(statistics())
        modify(data)
        backend, _ = client(data)
        with invocation("central") as scope:
            result = await delivery_reply(request(), scope, client=backend)
        assert result["status"] == "error"
        assert "INVALID_BACKEND_RESPONSE" in result["warnings"]


@pytest.mark.asyncio
async def test_sso_caller_credentials_take_precedence_and_old_cached_tools_are_rejected():
    token = CALLER.set(Caller("run-a", "http://caller", "caller-human", "", ""))
    try:
        backend, calls = client()
        with invocation("central") as scope:
            result = await delivery_reply(request(), scope, client=backend)
        assert result["status"] == "success"
        assert calls[0].headers["Authorization"] == "Bearer caller-human"
        assert calls[1].headers["Authorization"] == "Bearer caller-human"
        CALLER.set(Caller("run-b", "http://caller", "other-human", "", ""))
        with invocation("central") as scope:
            result = await delivery_reply(request(), scope, client=backend)
        assert result["status"] == "error" and len(calls) == 2
    finally:
        CALLER.reset(token)


def test_value_weighted_order_risk_json_keeps_guard_and_coverage():
    receipt = {
        "tool": "prediction_get_order_risk",
        "status": "success",
        "input": {},
        "error": None,
        "data": {
            "aggregation_policy": "remaining_value_weighted_v2",
            "status": "success",
            "representative_risk_score": 0.35,
            "highest_item_risk_score": 0.9,
            "scored_value_coverage": 1.0,
            "high_risk_item_count": 1,
            "unscored_item_count": 0,
        },
    }
    result = build_response("Theo dõi dòng có điểm cao", [receipt])
    assert result["datasets"][0]["rows"][0]["highest_item_risk_score"] == 0.9
    assert "ORDER_RISK_VALUE_WEIGHTED" in {w["code"] for w in result["warnings"]}
    assert "ORDER_RISK_MAX" not in {w["code"] for w in result["warnings"]}
