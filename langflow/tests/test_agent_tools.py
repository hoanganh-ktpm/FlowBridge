# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import json
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT.parent / "backend/src")]

from flowbridge_agent.config import BackendConfig  # noqa: E402
from flowbridge_agent.nodes import LunaModelBase, ProcurementAgentBase  # noqa: E402
from flowbridge_agent.tools import BackendTools, build_tools  # noqa: E402

PRODUCT = "00000001-0001-4001-8001-000000000001"
SUPPLIER = "00000002-0002-4002-8002-000000000002"
ORDER = "00000003-0003-4003-8003-000000000003"


def client(handler, **kwargs):
    return BackendTools(
        BackendConfig(user_token="unit-test-user", **kwargs), httpx.MockTransport(handler)
    )


@pytest.mark.asyncio
async def test_known_order_reads_risk_without_po_lookup_or_write():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "purchase_order_id": ORDER,
                "status": "no_open_items",
                "is_complete": True,
                "items": [],
            },
        )

    result = await client(handle).execute("prediction_get_order_risk", {"order_id": ORDER})
    assert result["status"] == "no_open_items"
    assert result["representative_risk_score"] is None
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", f"/api/v1/ml/purchase-orders/{ORDER}/risk")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"product_id": PRODUCT, "quantity": "100"},
        {"product_id": "Laptop", "quantity": "100", "requested_delivery_date": "2026-12-01"},
        {"product_id": PRODUCT, "quantity": 100, "requested_delivery_date": "2026-12-01"},
        {"product_id": PRODUCT, "quantity": "-1", "requested_delivery_date": "2026-12-01"},
        {
            "product_id": PRODUCT,
            "quantity": "100",
            "requested_delivery_date": "2026-12-01",
            "top_k": 11,
        },
        {
            "product_id": PRODUCT,
            "quantity": "100",
            "requested_delivery_date": "2026-12-01",
            "max_unit_price": "50",
        },
        {
            "product_id": PRODUCT,
            "quantity": "100",
            "requested_delivery_date": "2026-12-01",
            "api_origin": "https://example.com",
        },
    ],
)
async def test_invalid_ml_arguments_never_reach_network(args):
    def handle(request):
        pytest.fail("Invalid tool arguments must not call backend")

    assert (await client(handle).execute("prediction_rank_suppliers", args))[
        "code"
    ] == "INVALID_TOOL_ARGUMENTS"


@pytest.mark.asyncio
async def test_duplicate_read_uses_one_backend_request():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"items": [], "total": 0, "page": 1, "page_size": 100})

    api = client(handle)
    a = await api.execute("catalog_find_products", {"query": "Laptop"})
    b = await api.execute("catalog_find_products", {"query": "Laptop"})
    assert a == b and a["status"] == "not_found"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ambiguous_and_incomplete_catalog_do_not_invent_unique_match():
    def ambiguous(request):
        return httpx.Response(
            200,
            json={
                "items": [
                    {"product_id": PRODUCT, "product_name": "Laptop A", "unit": "cai"},
                    {"product_id": SUPPLIER, "product_name": "Laptop B", "unit": "cai"},
                ],
                "total": 2,
            },
        )

    result = await client(ambiguous).execute("catalog_find_products", {"query": "laptop"})
    assert result["status"] == "ambiguous" and len(result["items"]) == 2

    def incomplete(request):
        return httpx.Response(
            200, json={"items": [{"product_id": PRODUCT, "product_name": "Laptop"}], "total": 500}
        )

    result = await client(incomplete, max_pages=1).execute(
        "catalog_find_products", {"query": "Laptop"}
    )
    assert result["status"] == "truncated" and result["truncated"] is True


@pytest.mark.asyncio
async def test_permission_error_is_not_retried_or_reflected():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(403, json={"detail": "private-token-or-dsn"})

    api = client(handle)
    result = await api.execute("purchase_get_order", {"identifier": ORDER})
    assert result == {
        "status": "error",
        "code": "BACKEND_REQUEST_FAILED",
        "http_status": 403,
        "retryable": False,
        "data": None,
    }
    assert "private-token" not in json.dumps(result)
    await api.execute("purchase_get_order", {"identifier": ORDER})
    assert len(calls) == 1


def test_credentials_are_not_tool_arguments_and_rag_is_hidden_without_job():
    api = BackendTools(BackendConfig())
    assert build_tools("knowledge", api) == []
    for group in ("catalog", "purchase", "prediction"):
        for tool in build_tools(group, api):
            spec = convert_to_openai_tool(tool, strict=True)["function"]
            params = spec["parameters"]
            assert spec["strict"] and params["additionalProperties"] is False
            assert set(params["required"]) == set(params["properties"])
            assert not set(params["properties"]) & {
                "access_token",
                "api_origin",
                "role",
                "job_context",
                "feature_asof",
            }


def test_model_budget_and_bound_tools_without_any_openai_request():
    with patch(
        "flowbridge_agent.nodes.local_values", return_value={"OPENAI_API_KEY": "unit-test-key"}
    ):
        model = LunaModelBase(output_tokens=600).build_model()
    assert model.model_name == "gpt-5.6-luna"
    assert model.max_retries == 0 and model.max_tokens == 600
    assert model.reasoning_effort == "low"
    bound = model.bind_tools(build_tools("prediction", BackendTools(BackendConfig())))
    assert bound.kwargs["parallel_tool_calls"] is False
    assert all(t["function"]["strict"] for t in bound.kwargs["tools"])


def test_agent_has_finite_model_and_tool_limits_without_runtime_retries():
    agent = ProcurementAgentBase(max_iterations=4, handle_parsing_errors=False)
    middleware = agent._build_middleware(object(), allow_interrupts=False)
    names = [type(m).__name__ for m in middleware]
    assert "ModelCallLimitMiddleware" in names and "ToolCallLimitMiddleware" in names
    assert "ToolRetryMiddleware" not in names
