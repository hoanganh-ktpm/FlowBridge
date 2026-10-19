# SPDX-License-Identifier: GPL-3.0-or-later
"""Feature-history routing and evidence over mocked HTTP; no provider/model calls."""

import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.config import BackendConfig  # noqa: E402
from flowbridge_agent.frontend_response_builder import build_response  # noqa: E402
from flowbridge_agent.ver2_coordination import Request, invocation  # noqa: E402
from flowbridge_agent.ver2_data import DataBackend  # noqa: E402
from flowbridge_agent.ver2_history import history_reply  # noqa: E402
from flowbridge_agent.ver2_protocol_nodes import A2AProtocolAdapter  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402

PRODUCT, SUPPLIER = str(uuid4()), str(uuid4())


def reference():
    return {
        "policy": "supplier-product-unit-completed-v1",
        "product_id": PRODUCT,
        "supplier_id": SUPPLIER,
        "unit": "cái",
        "product_category": "office",
        "feature_asof": "2026-10-19T16:00:00Z",
        "history_window_days": 180,
        "exclude_item_id": None,
        "sample_count": 11,
        "fingerprint": "a" * 64,
    }


def payload():
    return {
        "status": "success",
        "reference": reference(),
        "reference_matches": True,
        "expected_count_matches": True,
        "scope": "company_procurement_reader",
        "items": [
            {
                "purchase_order_id": str(uuid4()),
                "po_item_id": str(uuid4()),
                "po_number": f"DH-{i:03}",
                "product_name": "Chuột Logitech B100",
                "unit": "cái",
                "ordered_quantity": "9",
                "order_date": "2026-09-01",
                "requested_delivery_date": "2026-09-10",
                "completion_date": "2026-09-13",
                "completion_known_at": "2026-09-13T03:00:00Z",
                "lead_days": 12,
                "days_late": 3,
                "delivered_late": True,
            }
            for i in range(11)
        ],
        "total_matches": 11,
        "page": 1,
        "page_size": 20,
        "next_page": None,
        "warnings": [],
    }


def backend(data=None, status=200, suppliers=None):
    calls = []

    def handle(req):
        calls.append(req)
        if req.url.path == "/api/v1/products":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "product_id": PRODUCT,
                            "product_code": "DEMO-P05",
                            "product_name": "Chuột Logitech B100",
                            "unit": "cái",
                        }
                    ],
                    "total": 1,
                },
            )
        if req.url.path == "/api/v1/suppliers":
            rows = (
                suppliers
                if suppliers is not None
                else [
                    {
                        "supplier_id": SUPPLIER,
                        "supplier_code": "NCC-01",
                        "supplier_name": "Thương mại An Phú — NCC-01",
                    }
                ]
            )
            return httpx.Response(200, json={"items": rows, "total": len(rows)})
        assert req.url.path == "/api/v1/ml/history"
        return httpx.Response(status, json=data if data is not None else payload())

    return DataBackend(
        BackendConfig(origin="http://test", user_token="human"), httpx.MockTransport(handle)
    ), calls


def request(**kwargs):
    return Request(
        task="history_samples",
        question="Cho tôi xem 11 bản ghi lịch sử nhé",
        business_date="2026-10-19",
        known={"product": "DEMO-P05", "supplier": "NCC-01", "expected_count": 11, **kwargs},
    )


@pytest.mark.asyncio
async def test_eleven_history_rows_reach_agent_and_frontend_without_rag_or_rescoring(monkeypatch):
    client, calls = backend()
    monkeypatch.setattr("flowbridge_agent.ver2_history.DataBackend", lambda _: client)
    original = AsyncMock(side_effect=AssertionError("Do not call child/RAG"))

    async def old_data(input_value: str):
        return await original(input_value)

    source = StructuredTool.from_function(
        coroutine=old_data, name="hoi_du_lieu", description="Data"
    )
    tool = A2AProtocolAdapter(
        source_tools=[source], caller_service="central", target_service="database"
    ).build_tools()[0]
    assert "hist_sample_count" in tool.description and "history_samples" in tool.description
    with invocation("central") as scope:
        reply = await tool.ainvoke(
            {"input_value": request(reference=reference()).model_dump_json()}
        )
        assert reply["status"] == "success"
        assert scope.run.model_calls == 0 and scope.run.a2a_calls == 1
        out = build_response("Đây là lịch sử tính đặc trưng", reply["results"])
    original.assert_not_awaited()
    assert len(out["datasets"][-1]["rows"]) == 11
    assert out["datasets"][-1]["rows"][0]["po_number"] == "DH-000"
    assert out["datasets"][-1]["context"]["reference_matches"] is True
    assert out["datasets"][-1]["context"]["history_reference"]["sample_count"] == 11
    assert calls[-1].headers["Authorization"] == "Bearer human"
    assert "X-Job-Context" not in calls[-1].headers


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_errors_preserve_http_status_and_do_not_turn_into_empty_history(status):
    client, _ = backend(status=status)
    with invocation("central") as scope:
        reply = await history_reply(request(), scope, client=client)
    assert reply["status"] == "error"
    assert reply["results"][-1]["error"]["http_status"] == status


@pytest.mark.asyncio
async def test_same_number_of_reconstructed_rows_is_not_presented_as_verified_old_cohort():
    data = payload()
    data.update(
        reference_matches=None, warnings=["HISTORY_RECONSTRUCTED_WITHOUT_ORIGINAL_REFERENCE"]
    )
    client, _ = backend(data)
    with invocation("central") as scope:
        reply = await history_reply(
            request(feature_asof="2026-10-19T16:00:00Z"), scope, client=client
        )
    out = build_response("Tái dựng lịch sử", reply["results"])
    assert "HISTORY_NOT_VERIFIED_ORIGINAL" in {w["code"] for w in out["warnings"]}
    assert out["datasets"][-1]["context"]["reference_matches"] is None


@pytest.mark.asyncio
async def test_unknown_supplier_and_invalid_arguments_do_not_fetch_unrelated_history():
    for known, suppliers in [({"supplier": None}, None), ({}, [])]:
        client, calls = backend(suppliers=suppliers)
        with invocation("central") as scope:
            reply = await history_reply(request(**known), scope, client=client)
        assert reply["status"] in {"needs_input", "not_found"}
        assert not any(req.url.path == "/api/v1/ml/history" for req in calls)


@pytest.mark.asyncio
async def test_rejects_history_for_a_different_supplier_and_corrupt_page():
    for update in (
        lambda d: d["reference"].update(supplier_id=str(uuid4())),
        lambda d: d.update(next_page=2),
    ):
        data = deepcopy(payload())
        update(data)
        client, _ = backend(data)
        with invocation("central") as scope:
            reply = await history_reply(request(), scope, client=client)
        assert reply["status"] == "error" and "INVALID_BACKEND_RESPONSE" in reply["warnings"]
