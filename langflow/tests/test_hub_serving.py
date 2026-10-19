# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Only native ASGI doubles/scripted tools: no OpenAI, Langflow server or database calls.
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from fastapi import Request as HTTPRequest
from langchain_core.messages import ToolMessage

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "langflow/runtime")]

from flowbridge_agent.caller import CALLER, Caller  # noqa: E402
from flowbridge_agent.serving import CallerMiddleware  # noqa: E402
from flowbridge_agent.tools import failure, tool_result  # noqa: E402
from flowbridge_agent.ver2_coordination import (  # noqa: E402
    PENDING,
    CoordinationMiddleware,
    Request,
    allowed_targets,
    invocation,
    prediction_preflight,
)
from test_sso_serving import PATH, SECRET, async_test, headers, payload  # noqa: E402
from test_ver2_coordination import call  # noqa: E402

from flowbridge.agent_hub.activities import validate_activity  # noqa: E402
from flowbridge.agent_hub.contract import validate  # noqa: E402


def hub_headers(selected):
    return {
        **headers(),
        "X-FlowBridge-Protocol": "agent-hub-v1",
        "X-FlowBridge-Selected-Agents": json.dumps(selected),
        "X-FlowBridge-Business-Date": "2026-10-19",
    }


@async_test
async def test_hub_stream_contains_real_tool_timing_and_error_attribution():
    native = FastAPI()
    result = json.loads(
        (ROOT / "langflow/examples/frontend_response/empty.json").read_text("utf-8")
    )

    @native.post(PATH)
    async def execute(request: HTTPRequest):
        body = (await request.json())["input_request"]
        assert body["output_component"] == "FrontendResponse-ver2-central"
        assert body["output_type"] == "any"
        assert CALLER.get().selected_agents == ("knowledge",)
        assert "x-flowbridge-access-token" not in request.headers
        with invocation("central"):
            token = PENDING.set(
                (
                    "knowledge",
                    Request(task="retrieve", question="Tài liệu", business_date="2026-10-19"),
                )
            )
            try:
                with invocation("knowledge") as scope:
                    handler = AsyncMock(
                        return_value=ToolMessage(
                            content=json.dumps(
                                tool_result(
                                    "knowledge_search_supplier", {}, failure("UPSTREAM_TIMEOUT")
                                )
                            ),
                            tool_call_id="call-knowledge_search_supplier",
                        )
                    )
                    await CoordinationMiddleware().awrap_tool_call(
                        call("knowledge_search_supplier"), handler
                    )
                    scope.activity_status = "failed"
                    scope.activity_error = "UPSTREAM_TIMEOUT"
            finally:
                PENDING.reset(token)
        return {
            "outputs": [
                {
                    "outputs": [
                        {
                            "component_id": "FrontendResponse-ver2-central",
                            "results": {"response": {"data": result}},
                        }
                    ]
                }
            ]
        }

    app = CallerMiddleware(native, secret=SECRET, api_origin="http://api.test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lf.test"
    ) as client:
        reply = await client.post(PATH, headers=hub_headers(["knowledge"]), json=payload())
    assert reply.status_code == 200, reply.text
    frames = [json.loads(line) for line in reply.text.splitlines() if line.strip()]
    assert frames[-1]["kind"] == "result"
    events = []
    for frame in frames[:-1]:
        validate_activity(frame, events, ["knowledge"])
        event = {
            "schema_version": "1.0",
            "id": f"run_test:{len(events) + 1}",
            "sequence": len(events) + 1,
            "run_id": "run_test",
            **{k: frame[k] for k in ("type", "data", "occurred_at")},
        }
        validate(event, "RunEvent")
        events.append(event)
    tool = next(e["data"] for e in events if e["type"] == "tool.completed")
    assert tool["agent"] == "knowledge"
    assert tool["tool"] == "knowledge_search_supplier"
    assert tool["outcome"] == "failed"
    assert tool["duration_ms"] >= 0
    assert tool["error"]["code"] == "UPSTREAM_TIMEOUT"
    assert "alice" not in reply.text and SECRET not in reply.text
    assert CALLER.get() is None


@async_test
async def test_selection_is_isolated_and_dependencies_stay_inside_langflow():
    async def execute(selected):
        token = CALLER.set(Caller("exec", "http://api.test", "fixture", selected_agents=selected))
        try:
            await asyncio.sleep(0)
            assert allowed_targets("central") == set(selected)
            assert allowed_targets("prediction") == {"database"}
            assert allowed_targets("knowledge") == {"database"}
            with invocation("central") as scope:
                request = Request(
                    task="rank_suppliers",
                    question="Băng keo",
                    business_date="2026-10-19",
                    known={"product": "DEMO-P03"},
                )
                if selected == ("prediction",):
                    assert prediction_preflight(request, scope) is None
        finally:
            CALLER.reset(token)

    await asyncio.gather(execute(("prediction",)), execute(("knowledge",)), execute(()))
    assert allowed_targets("central") == {"database", "prediction", "knowledge"}


@async_test
async def test_unselected_agent_blocked_before_native_tool_or_model():
    observed = []
    token = CALLER.set(
        Caller(
            "exec",
            "http://api.test",
            "fixture",
            selected_agents=("knowledge",),
            emit=observed.append,
        )
    )
    try:
        with invocation("central"):
            handler = AsyncMock()
            reply = await CoordinationMiddleware().awrap_tool_call(call("hoi_du_doan"), handler)
            assert json.loads(reply.content)["error"]["code"] == "TOOL_OUTSIDE_SERVICE"
            handler.assert_not_called()
        failed = next(e["data"] for e in observed if e["type"] == "tool.completed")
        assert failed["agent"] == "prediction"
        assert failed["outcome"] == "failed"
        assert failed["error"]["code"] == "TOOL_OUTSIDE_SERVICE"
    finally:
        CALLER.reset(token)
