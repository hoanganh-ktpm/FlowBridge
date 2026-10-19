# SPDX-License-Identifier: GPL-3.0-or-later
import json
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from flowbridge_agent.config import BackendConfig
from flowbridge_agent.frontend_response_builder import build_response
from flowbridge_agent.ver2_coordination import Request, invocation
from flowbridge_agent.ver2_data import DataBackend
from flowbridge_agent.ver2_multi_item_orders import multi_item_reply
from flowbridge_agent.ver2_protocol_nodes import A2AProtocolAdapter
from langchain_core.tools import StructuredTool

P1, P2, PO, SUPPLIER = [str(uuid4()) for _ in range(4)]


def payload():
    return {
        "min_distinct_products": 2,
        "page": 1,
        "page_size": 1,
        "total": 1,
        "items": [
            {
                "purchase_order_id": PO,
                "po_number": "PO-MULTI-TEST",
                "supplier_id": SUPPLIER,
                "currency": "VND",
                "status": "issued",
                "items": [
                    {
                        "product_id": P1,
                        "unit": "cái",
                        "ordered_quantity": "89",
                        "unit_price": "200000",
                    },
                    {
                        "product_id": P2,
                        "unit": "ram",
                        "ordered_quantity": "50",
                        "unit_price": "90000",
                    },
                ],
            }
        ],
    }


def request(**known):
    return Request(
        task="find_multi_item_orders",
        question="Cho một mã đơn nhiều món hàng",
        business_date="2026-10-20",
        known=known,
    )


def client(value=None, code=200):
    calls = []

    def handle(req):
        calls.append(req)
        if req.url.path == "/api/v1/products":
            products = [
                {
                    "product_id": P1,
                    "product_code": "P01",
                    "product_name": "Bàn phím",
                    "unit": "cái",
                },
                {"product_id": P2, "product_code": "P02", "product_name": "Giấy A4", "unit": "ram"},
            ]
            return httpx.Response(200, json={"items": products, "total": 2})
        assert req.url.path == "/api/v1/purchase-orders"
        assert dict(req.url.params) == {"min_distinct_products": "2", "page": "1", "page_size": "1"}
        return httpx.Response(code, json=value if value is not None else payload())

    return DataBackend(BackendConfig(user_token="scoped-human"), httpx.MockTransport(handle)), calls


@pytest.mark.asyncio
async def test_a2a_finds_real_order_and_frontend_keeps_distinct_units_without_child_model(
    monkeypatch,
):
    api, calls = client()
    monkeypatch.setattr("flowbridge_agent.ver2_multi_item_orders.DataBackend", lambda _: api)
    original_handler = AsyncMock(side_effect=AssertionError("No child model expected"))

    async def original_call(input_value: str):
        return await original_handler(input_value)

    original = StructuredTool.from_function(
        coroutine=original_call, name="hoi_du_lieu", description="Data"
    )
    tool = A2AProtocolAdapter(
        source_tools=[original], caller_service="central", target_service="database"
    ).build_tools()[0]
    assert "find_multi_item_orders" in tool.description
    assert "tồn kho, tổng lượng đã đặt hay lượng chưa nhận" in tool.description
    with invocation("central") as scope:
        value = await tool.ainvoke({"input_value": request().model_dump_json()})
        assert value["status"] == "success" and value["resolved"] == {}
        assert scope.run.a2a_calls == 1 and scope.run.model_calls == 0
        data = value["results"][0]["data"]
        assert data["items"][0]["po_number"] == "PO-MULTI-TEST"
        assert data["items"][0]["distinct_product_count"] == 2
        frontend = build_response("Đơn nhiều mặt hàng", value["results"])
    original_handler.assert_not_awaited()
    rows = frontend["datasets"][0]["rows"]
    assert [r["product_name"] for r in rows] == ["Bàn phím", "Giấy A4"]
    assert {r["unit"] for r in rows} == {"cái", "ram"}
    assert frontend["charts"] == []  # Do not sum or compare quantities across units.
    assert len(calls) == 2 and all(
        r.headers["Authorization"] == "Bearer scoped-human" for r in calls
    )


@pytest.mark.asyncio
async def test_zero_filtered_matches_is_not_a_truncated_500_order_scan_or_question():
    api, calls = client({**payload(), "items": [], "total": 0})
    with invocation("central") as scope:
        value = await multi_item_reply(request(), scope, client=api)
    assert value["status"] == "not_found" and value["questions"] == []
    assert value["results"][0]["data"]["total_matches"] == 0
    assert not value["results"][0]["data"]["has_more"] and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["old_backend", "one_product", "wrong_count", "bad_id", "repeated_lines"]
)
async def test_ignored_filter_and_malformed_pages_never_become_example_orders(fault):
    raw = deepcopy(payload())
    if fault == "old_backend":
        raw.pop("min_distinct_products")
    elif fault == "one_product":
        raw["items"][0]["items"] = raw["items"][0]["items"][:1]
    elif fault == "repeated_lines":
        raw["items"][0]["items"][1]["product_id"] = P1
    elif fault == "wrong_count":
        raw["items"] = []
    else:
        raw["items"][0]["purchase_order_id"] = "bad"
    api, calls = client(raw)
    with invocation("central") as scope:
        value = await multi_item_reply(request(), scope, client=api)
    assert value["status"] == "error" and value["warnings"] == ["INVALID_BACKEND_RESPONSE"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_denied_call_never_scans_other_pages_or_uses_other_identity():
    api, calls = client({"private": "not-public"}, 403)
    with invocation("central") as scope:
        value = await multi_item_reply(request(), scope, client=api)
    assert value["results"][0]["error"]["http_status"] == 403
    assert "not-public" not in json.dumps(value) and len(calls) == 1


@pytest.mark.asyncio
async def test_filters_cache_and_a2a_budget_are_scoped_to_current_turn():
    api, calls = client()
    with invocation("central") as scope:
        invalid = await multi_item_reply(request(supplier="NCC-01"), scope, client=api)
        assert invalid["status"] == "needs_input" and not calls
        value = await multi_item_reply(request(), scope, client=api)
        scope.run.a2a_calls = scope.run.max_a2a
        assert await multi_item_reply(request(), scope, client=api) == value
        exhausted = await multi_item_reply(request(page=2), scope, client=api)
        assert exhausted["warnings"] == ["A2A_BUDGET_EXHAUSTED"]
