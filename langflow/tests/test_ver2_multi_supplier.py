# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from flowbridge_agent.config import BackendConfig
from flowbridge_agent.frontend_response_builder import build_response
from flowbridge_agent.ver2_coordination import Request, invocation
from flowbridge_agent.ver2_data import DataBackend
from flowbridge_agent.ver2_multi_supplier import multi_supplier_reply
from flowbridge_agent.ver2_protocol_nodes import A2AProtocolAdapter
from langchain_core.tools import StructuredTool

RID, O1, O2, S1, S2 = [str(uuid4()) for _ in range(5)]


def payload():
    return {
        "items": [
            {
                "request_id": RID,
                "request_code": "DEMO-PR-MULTI-003",
                "distinct_supplier_count": 2,
                "purchase_orders": [
                    {
                        "purchase_order_id": o,
                        "request_id": RID,
                        "po_number": f"DEMO-PO-MULTI-{n}",
                        "supplier_id": s,
                        "status": "issued",
                        "currency": "VND",
                        "total_amount": "100000",
                    }
                    for o, s, n in [(O1, S1, "003"), (O2, S2, "004")]
                ],
            }
        ],
        "total": 1,
        "min_distinct_suppliers": 2,
        "page": 1,
        "page_size": 1,
    }


def request(task="find_multi_supplier_requests", **known):
    return Request(
        task=task,
        question="Tìm đơn hàng gồm nhiều nhà cung cấp",
        business_date="2026-10-20",
        known=known,
    )


def client(value=None, denied=False):
    calls = []

    def handle(req):
        calls.append(req)
        if denied:
            return httpx.Response(403, json={"private": "not-public"})
        if req.url.path == "/api/v1/suppliers":
            return httpx.Response(
                200,
                json={
                    "total": 2,
                    "items": [
                        {"supplier_id": s, "supplier_code": c, "supplier_name": name}
                        for s, c, name in [(S1, "NCC-02", "Minh Khang"), (S2, "NCC-03", "Nam Việt")]
                    ],
                },
            )
        assert req.url.path == "/api/v1/purchase-requests"
        assert req.url.params["min_distinct_suppliers"] == "2"
        if req.url.params.get("request_code"):
            assert req.url.params["request_code"] == "DEMO-PR-MULTI-003"
        return httpx.Response(200, json=value if value is not None else payload())

    return DataBackend(BackendConfig(user_token="scoped-human"), httpx.MockTransport(handle)), calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task", ["find_multi_supplier_requests", "find_multi_supplier_orders", "multi_supplier_orders"]
)
async def test_supplier_group_reaches_a2a_and_frontend_without_changing_nodes_or_child_model(
    monkeypatch, task
):
    api, calls = client()
    monkeypatch.setattr("flowbridge_agent.ver2_multi_supplier.DataBackend", lambda _: api)
    original_handler = AsyncMock(side_effect=AssertionError("No child model expected"))

    async def original_call(input_value: str):
        return await original_handler(input_value)

    source = StructuredTool.from_function(
        coroutine=original_call, name="hoi_du_lieu", description="Old Data"
    )
    tool = A2AProtocolAdapter(
        source_tools=[source], caller_service="central", target_service="database"
    ).build_tools()[0]
    assert "find_multi_supplier_requests" in tool.description
    with invocation("central") as scope:
        value = await tool.ainvoke({"input_value": request(task).model_dump_json()})
        assert value["status"] == "success" and value["resolved"] == {}
        assert scope.run.a2a_calls == 1 and scope.run.model_calls == 0
        data = value["results"][0]["data"]
        assert data["items"][0]["request_code"] == "DEMO-PR-MULTI-003"
        assert {o["supplier_name"] for o in data["items"][0]["purchase_orders"]} == {
            "Minh Khang",
            "Nam Việt",
        }
        frontend = build_response("Một yêu cầu được tách thành hai đơn", value["results"])
    original_handler.assert_not_awaited()
    assert len(calls) == 2 and all(
        r.headers["Authorization"] == "Bearer scoped-human" for r in calls
    )
    rows = frontend["datasets"][0]["rows"]
    assert {r["request_code"] for r in rows} == {"DEMO-PR-MULTI-003"}
    assert len({r["supplier_code"] for r in rows}) == 2
    corrupted = deepcopy(value["results"])
    corrupted[0]["data"]["items"][0]["purchase_orders"] = ["invalid"]
    bad = build_response("", corrupted)
    assert any(w["code"] == "INVALID_TOOL_DATA" for w in bad["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    ["old_backend", "foreign_request", "same_supplier", "bad_count", "cancelled", "duplicate"],
)
async def test_supplier_group_never_mixes_requests_or_treats_same_supplier_as_many(fault):
    raw = deepcopy(payload())
    group = raw["items"][0]
    if fault == "old_backend":
        raw.pop("min_distinct_suppliers")
    elif fault == "foreign_request":
        group["purchase_orders"][1]["request_id"] = str(uuid4())
    elif fault == "same_supplier":
        group["purchase_orders"][1]["supplier_id"] = S1
    elif fault == "bad_count":
        group["distinct_supplier_count"] = 3
    elif fault == "cancelled":
        group["purchase_orders"][1]["status"] = "cancelled"
    else:
        group["purchase_orders"][1]["purchase_order_id"] = O1
    api, calls = client(raw)
    with invocation("central") as scope:
        value = await multi_supplier_reply(request(), scope, client=api)
    assert value["status"] == "error" and value["warnings"] == ["INVALID_BACKEND_RESPONSE"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_empty_scope_and_denial_are_not_fake_groups():
    for denied in [False, True]:
        api, calls = client({**payload(), "items": [], "total": 0}, denied=denied)
        with invocation("central") as scope:
            value = await multi_supplier_reply(request(), scope, client=api)
        assert value["status"] == ("error" if denied else "not_found")
        assert value["questions"] == [] and len(calls) == 1
        assert "not-public" not in str(value)


@pytest.mark.asyncio
async def test_exact_request_code_and_cache_keep_same_selection_and_current_budget():
    api, calls = client()
    with invocation("central") as scope:
        req = request(request_code="demo-pr-multi-003")
        value = await multi_supplier_reply(req, scope, client=api)
        scope.run.a2a_calls = scope.run.max_a2a
        assert await multi_supplier_reply(req, scope, client=api) == value
        assert len(calls) == 2
