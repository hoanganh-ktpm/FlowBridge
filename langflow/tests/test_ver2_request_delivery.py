# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from flowbridge_agent.config import BackendConfig
from flowbridge_agent.frontend_response_builder import build_response
from flowbridge_agent.tools import tool_result
from flowbridge_agent.ver2_data import DataBackend
from flowbridge_agent.ver2_request_delivery import request_delivery


def scenario(statuses=("open_not_due", "open_not_due")):
    request = {"request_id": str(uuid4()), "request_code": "DEMO-PR-MULTI-003", "items": []}
    headers, details = [], {}
    for number, status in enumerate(statuses, 3):
        header = {
            "purchase_order_id": str(uuid4()),
            "request_id": request["request_id"],
            "po_number": f"DEMO-PO-MULTI-{number:03}",
            "supplier_id": str(uuid4()),
            "status": "issued",
        }
        headers.append(header)
        details[header["purchase_order_id"]] = {
            **header,
            "supplier_code": f"NCC-{number - 1:02}",
            "supplier_name": f"Nhà cung cấp {number}",
            "delivery_evidence_status": "verified",
            "delivery_evidence": {
                "as_of": f"2026-10-20T00:00:0{number}Z",
                "complete": True,
                "orders": {
                    "items": [
                        {
                            "purchase_order_id": header["purchase_order_id"],
                            "delivery_status": status,
                            "max_days_late": 3 if status.endswith("late") else 0,
                        }
                    ]
                },
            },
        }
    page = {
        "request_id": request["request_id"],
        "items": headers,
        "total": len(headers),
        "page": 1,
        "page_size": 10,
    }

    async def lookup(kind, identifier):
        assert kind == "purchase-orders"
        return deepcopy(details[identifier])

    client = SimpleNamespace(
        request=AsyncMock(return_value=page), purchase_lookup=AsyncMock(side_effect=lookup)
    )
    return request, client, details, page


@pytest.mark.asyncio
async def test_group_followup_reads_both_children_and_no_completed_orders_means_null_rate():
    request, client, _, _ = scenario()
    result = await request_delivery(client, request)
    assert result["status"] == "verified" and result["complete"]
    assert [o["po_number"] for o in result["orders"]] == ["DEMO-PO-MULTI-003", "DEMO-PO-MULTI-004"]
    assert result["summary"]["completed_orders"] == 0
    assert result["summary"]["late_completed_orders_percent"] is None
    assert result["summary"]["counts"]["open_not_due"] == 2
    assert result["summary"]["counts"]["open_overdue"] == 0
    client.request.assert_awaited_once_with(
        "GET",
        "/api/v1/purchase-orders",
        params={"request_id": request["request_id"], "page": 1, "page_size": 10},
    )
    assert client.purchase_lookup.await_count == 2
    frontend = build_response(
        "Hai đơn chưa nhận; chưa đến hạn.",
        [tool_result("purchase_get_request", {}, {**request, "request_delivery_evidence": result})],
    )
    rows = frontend["datasets"][0]["rows"]
    assert len(rows) == 2 and all(r["delivered_late"] is None for r in rows)
    assert rows[0]["po_number"] == "DEMO-PO-MULTI-003"
    assert frontend["datasets"][0]["context"]["count_unit"] == "đơn đặt hàng"


@pytest.mark.asyncio
async def test_group_rate_uses_completed_order_denominator_not_open_orders():
    request, client, _, _ = scenario(("completed_late", "completed_on_time", "open_overdue"))
    result = await request_delivery(client, request)
    assert result["summary"]["completed_orders"] == 2
    assert result["summary"]["late_completed_orders_percent"] == 50.0
    assert result["summary"]["counts"]["open_overdue"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["filter", "parent", "duplicate", "total", "denied"])
async def test_wrong_or_unavailable_parent_page_never_fetches_other_orders(change):
    request, client, _, page = scenario()
    if change == "filter":
        page.pop("request_id")
    elif change == "parent":
        page["items"][0]["request_id"] = str(uuid4())
    elif change == "duplicate":
        page["items"][1] = page["items"][0]
    elif change == "total":
        page["total"] = True
    else:
        client.request.return_value = {"status": "error", "http_status": 403}
    result = await request_delivery(client, request)
    assert result["status"] == "unavailable" and result["summary"] is None
    client.purchase_lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["parent", "evidence", "incomplete", "missing", "truncated"])
async def test_partial_or_changed_children_do_not_produce_a_group_rate(change):
    request, client, details, page = scenario()
    child = next(iter(details.values()))
    if change == "parent":
        child["request_id"] = str(uuid4())
    elif change == "evidence":
        child["delivery_evidence_status"] = "unavailable"
    elif change == "incomplete":
        child["delivery_evidence"]["complete"] = False
    elif change == "missing":
        child["delivery_evidence"]["orders"]["items"] = []
    else:
        # There may be more than ten children: do not turn a bounded page into
        # an apparently complete group statistic.
        page["items"] = [deepcopy(page["items"][0]) for _ in range(10)]
        for header in page["items"]:
            header["purchase_order_id"] = str(uuid4())
            details[header["purchase_order_id"]] = {**deepcopy(child), **header}
        page["total"] = 11
    result = await request_delivery(client, request)
    assert result["status"] == "partial" and result["summary"] is None
    assert not result["complete"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,code",
    [
        ("purchase_get_request", "DEMO-PO-MULTI-003"),
        ("purchase_get_order", "DEMO-PR-MULTI-003"),
    ],
)
async def test_wrong_resource_code_is_not_reported_as_missing_or_forbidden(action, code):
    transport = httpx.MockTransport(lambda r: pytest.fail("Must reject routing error before HTTP"))
    client = DataBackend(BackendConfig(user_token="test"), transport)
    result = await client.execute(action, {"identifier": code})
    assert result["status"] == "error" and result["code"] == "PURCHASE_RESOURCE_MISMATCH"
    assert result["http_status"] is None


@pytest.mark.asyncio
async def test_actual_request_handoff_selector_runs_once_without_child_model(monkeypatch):
    from flowbridge_agent.ver2_coordination import Request, invocation
    from flowbridge_agent.ver2_protocol_nodes import A2AProtocolAdapter
    from langchain_core.tools import StructuredTool

    request, client, _, _ = scenario()
    evidence = await request_delivery(client, request)
    api = SimpleNamespace(
        execute=AsyncMock(
            return_value={
                **request,
                "status": "ordered",
                "request_delivery_evidence": evidence,
            }
        )
    )
    monkeypatch.setattr("flowbridge_agent.ver2_data.DataBackend", lambda _: api)
    original = AsyncMock(side_effect=AssertionError("No child model expected"))

    async def invoke(input_value: str):
        return await original(input_value)

    source = StructuredTool.from_function(coroutine=invoke, name="hoi_du_lieu", description="Old")
    tool = A2AProtocolAdapter(
        source_tools=[source], caller_service="central", target_service="database"
    ).build_tools()[0]
    payload = Request(
        task="request",
        question="đơn này giao chưa, tỷ lệ trễ",
        business_date="2026-10-20",
        known={"identifier": "DEMO-PR-MULTI-003"},
    )
    with invocation("central") as scope:
        result = await tool.ainvoke({"input_value": payload.model_dump_json()})
        repeated = await tool.ainvoke({"input_value": payload.model_dump_json()})
        assert result == repeated and result["status"] == "success"
        assert result["resolved"] == {"request_id": request["request_id"]}
        assert scope.run.model_calls == 0 and scope.run.a2a_calls == 1
        assert (
            result["results"][0]["data"]["request_delivery_evidence"]["summary"][
                "late_completed_orders_percent"
            ]
            is None
        )
    api.execute.assert_awaited_once_with(
        "purchase_get_request", {"identifier": "DEMO-PR-MULTI-003"}
    )
    original.assert_not_awaited()
