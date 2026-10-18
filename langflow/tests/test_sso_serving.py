# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Exercise ASGI context propagation and native Internal A2A without model or network IO.
import asyncio
import json
import sys
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request

sys.path[:0] = [
    str(Path(__file__).resolve().parents[1] / "runtime"),
    str(Path(__file__).resolve().parents[2] / "backend"),
]

from flowbridge_agent.caller import CALLER, Caller, knowledge_available  # noqa: E402
from flowbridge_agent.config import BackendConfig  # noqa: E402
from flowbridge_agent.serving import CallerMiddleware  # noqa: E402
from flowbridge_agent.tools import BackendTools  # noqa: E402
from flowbridge_agent.ver2_data import DataBackend  # noqa: E402
from lfx.components.models_and_agents.a2a_agent import A2AAgentComponent  # noqa: E402

SECRET = "s" * 48
PATH = "/api/v1/run/flowbridge-ver2-central"


def async_test(function):
    @wraps(function)
    def execute(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return execute


@async_test
async def test_native_run_endpoint_accepts_bridge_body_and_cold_context(monkeypatch):
    from langflow.api.v1 import endpoints

    native = FastAPI()
    native.include_router(endpoints.router, prefix="/api/v1")
    user_id = uuid4()
    user = SimpleNamespace(id=user_id)
    flow = SimpleNamespace(id=uuid4(), user_id=user_id, workspace_id=None, folder_id=None)

    async def get_user():
        return user

    async def get_flow():
        return flow

    async def allow(*args, **kwargs):
        return None

    observed = []
    bound_request = str(uuid4())

    async def internal(**kwargs):
        observed.append(kwargs)
        assert kwargs["input_request"].input_value.endswith("hello")
        assert bound_request in kwargs["input_request"].input_value
        assert kwargs["context"] == {"flowbridge_execution": CALLER.get().execution_id}
        assert kwargs["stream"] is False
        return {"outputs": [{"outputs": [{"results": {"message": {"text": "native-bound"}}}]}]}

    native.dependency_overrides[endpoints.api_key_security] = get_user
    native.dependency_overrides[endpoints.get_flow_for_api_key_user] = get_flow
    monkeypatch.setattr(endpoints, "ensure_flow_permission", allow)
    monkeypatch.setattr(endpoints, "_run_flow_internal", internal)
    app = CallerMiddleware(native, secret=SECRET, api_origin="http://api.test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lf.test"
    ) as c:
        result = await c.post(
            PATH,
            headers={**headers(), "X-FlowBridge-Request-Context": bound_request},
            json=payload(),
        )
    assert result.status_code == 200, result.text
    assert result.json()["answer"] == "native-bound"
    assert result.json()["request_ids"] == [bound_request]
    assert len(observed) == 1


def headers(user="alice"):
    return {
        "X-FlowBridge-Bridge-Secret": SECRET,
        "X-FlowBridge-Access-Token": user,
        "x-api-key": "native-api-key",
    }


def payload():
    return {
        "input_value": "hello",
        "session_id": str(uuid4()),
        "input_type": "chat",
        "output_type": "chat",
    }


def test_historical_prediction_receipt_tracks_parent_and_ignores_error_correlation():
    from flowbridge_agent.caller import observe

    caller = Caller(str(uuid4()), "http://api.test", "alice")
    token = CALLER.set(caller)
    order_id = str(uuid4())
    try:
        observe(
            "/api/v1/ml/predictions/id",
            {
                "prediction_id": str(uuid4()),
                "purchase_order_id": order_id,
                "status": "succeeded",
            },
        )
        observe(
            "/api/v1/products",
            {
                "status": "error",
                "request_id": str(uuid4()),
                "code": "BACKEND_REQUEST_FAILED",
                "http_status": 401,
            },
        )
        assert caller.order_ids == {order_id}
        assert not caller.request_ids
        assert caller.denied_status == 401
    finally:
        CALLER.reset(token)


@async_test
async def test_native_a2a_retains_context_and_concurrent_callers_are_isolated(monkeypatch):
    import lfx.helpers

    seen, clean_requests = [], []

    async def child_run(**kwargs):
        caller = CALLER.get()
        assert caller is not None
        await asyncio.sleep(0)

        async def backend(request):
            await asyncio.sleep(0)
            seen.append((caller.execution_id, request.url.host, request.headers["Authorization"]))
            return httpx.Response(200, json={"request_id": str(uuid4()), "request_code": "PR"})

        # Stored local-test credentials must be overridden even in a sub-agent.
        tool = BackendTools(
            BackendConfig(origin="http://127.0.0.1:8001", user_token="local"),
            transport=httpx.MockTransport(backend),
        )
        await tool.request("GET", "/api/v1/purchase-requests/1")
        return caller.user_token

    monkeypatch.setattr(lfx.helpers, "run_flow", child_run)
    native = FastAPI()

    @native.post(PATH)
    async def run(request: Request):
        body = await request.json()
        assert not any(k.startswith("x-flowbridge-") for k in request.headers)
        assert request.headers["x-api-key"] == "native-api-key"
        assert set(body) == {"input_request", "context"}
        assert body["input_request"]["tweaks"]["ChatInput-v2-central"] == {
            "should_store_message": False
        }
        assert body["input_request"]["tweaks"]["ChatOutput-v2-central"] == {
            "should_store_message": False
        }
        clean_requests.append(json.dumps(body))
        bridge = SimpleNamespace(
            input_value="business input",
            user_id=str(uuid4()),
            _isolated_sub_session=lambda flow: CALLER.get().execution_id + flow,
        )
        user = await A2AAgentComponent._run_target_isolated(bridge, object(), "child")
        return {"outputs": [{"outputs": [{"results": {"message": {"text": "reply " + user}}}]}]}

    app = CallerMiddleware(native, secret=SECRET, api_origin="http://api.test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lf.test"
    ) as c:
        left, right = payload(), payload()
        a, b = await asyncio.gather(
            c.post(PATH, headers=headers(), json=left),
            c.post(PATH, headers=headers("bob"), json=right),
        )
        assert a.status_code == b.status_code == 200
        assert a.json()["answer"] == "reply alice"
        assert b.json()["answer"] == "reply bob"
        assert len(a.json()["request_ids"]) == len(b.json()["request_ids"]) == 1
        assert a.json()["request_ids"] != b.json()["request_ids"]
        assert set(seen) == {
            (left["session_id"], "api.test", "Bearer alice"),
            (right["session_id"], "api.test", "Bearer bob"),
        }
        assert all(
            "alice" not in raw and "bob" not in raw and SECRET not in raw for raw in clean_requests
        )
        probe = await c.get("/_ops/flowbridge", headers={"X-FlowBridge-Bridge-Secret": SECRET})
        assert probe.json()["protocol"] == "flowbridge-caller-v1"
    assert CALLER.get() is None


@async_test
async def test_invalid_bridge_requests_never_reach_flow():
    calls = []

    async def native(scope, receive, send):
        calls.append(scope)

    app = CallerMiddleware(native, secret=SECRET, api_origin="http://api.test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lf.test"
    ) as c:
        assert (
            await c.post(
                PATH, headers={**headers(), "X-FlowBridge-Bridge-Secret": "bad"}, json=payload()
            )
        ).status_code == 403
        assert (
            await c.post(PATH + "?stream=true", headers=headers(), json=payload())
        ).status_code == 403
        assert (
            await c.post(PATH, headers=headers(), json={**payload(), "tweaks": {}})
        ).status_code == 422
        duplicate = list(headers().items()) + [("X-FlowBridge-Access-Token", "other")]
        assert (await c.post(PATH, headers=duplicate, json=payload())).status_code == 403
        assert (
            await c.post(PATH, headers=headers(), json={**payload(), "session_id": "bad"})
        ).status_code == 422
    assert not calls
    assert CALLER.get() is None


@async_test
async def test_tool_denial_is_not_hidden_by_model_answer_and_context_resets():
    native = FastAPI()

    @native.post(PATH)
    async def run():
        tool = BackendTools(
            BackendConfig(user_token="old"),
            transport=httpx.MockTransport(lambda r: httpx.Response(401, text="secret error")),
        )
        await tool.request("GET", "/api/v1/products")
        return {"outputs": [{"outputs": [{"results": {"message": {"text": "invented answer"}}}]}]}

    app = CallerMiddleware(native, secret=SECRET, api_origin="http://api.test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lf.test"
    ) as c:
        result = await c.post(PATH, headers=headers(), json=payload())
    assert result.status_code == 401
    assert result.headers["x-flowbridge-tool-denied"] == "401"
    assert "invented" not in result.text and "secret" not in result.text
    assert CALLER.get() is None


@async_test
async def test_cached_data_tool_cannot_replay_across_execution_contexts():
    first = CALLER.set(Caller(str(uuid4()), "http://api.test", "alice"))
    tool = DataBackend(BackendConfig(user_token="local"))
    tool.cache['["catalog_list", {"resource": "products"}]'] = {"secret": "prior"}
    assert not knowledge_available(BackendConfig(ai_token="local-ai", job_context=str(uuid4())))
    CALLER.reset(first)
    second = CALLER.set(Caller(str(uuid4()), "http://api.test", "bob"))
    try:
        result = await tool.execute("catalog_list", {"resource": "products"})
        assert result["code"] == "TOOL_CONTEXT_EXPIRED"
        assert "prior" not in json.dumps(result)
    finally:
        CALLER.reset(second)


@async_test
async def test_agent_never_reads_or_writes_native_memory_during_serving(monkeypatch):
    from flowbridge_agent.ver2_agent import OpenAICompatibleAgent

    agent = OpenAICompatibleAgent()
    token = CALLER.set(Caller(str(uuid4()), "http://api.test", "alice"))
    try:
        assert await agent.get_memory_data() == []
        sentinel = object()
        assert await agent.send_message(sentinel) is sentinel
    finally:
        CALLER.reset(token)
