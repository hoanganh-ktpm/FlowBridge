# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt.tool_node import ToolCallRequest
from lfx.components.models_and_agents.a2a_agent import A2AAgentComponent
from lfx.components.processing.converter import TypeConverterComponent
from lfx.schema.message import Message
from pydantic import Field

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.tools import tool_result  # noqa: E402
from flowbridge_agent.ver2_coordination import (  # noqa: E402
    PENDING,
    CoordinationMiddleware,
    Reply,
    Request,
    collect,
    finish_reply,
    invocation,
    loads,
)
from flowbridge_agent.ver2_coordination_nodes import BoundedA2AAgent  # noqa: E402
from flowbridge_agent.ver2_protocol_nodes import (  # noqa: E402
    A2AProtocolAdapter,
    A2AReplyEnvelope,
    ProtocolInput,
    matching_receipts,
    normalize_request,
)

PRODUCT = "024b8d0d-3548-54ad-8733-8385905fdbe1"
ENTITY = tool_result(
    "catalog_resolve_entities",
    {"product": "DEMO-P03", "supplier": None},
    {
        "status": "success",
        "resolved": {
            "product_id": PRODUCT,
            "product_code": "DEMO-P03",
            "product_name": "Băng keo vàng",
            "unit": "cuộn",
        },
        "entities": {"product": {"status": "success", "match_count": 1, "truncated": False}},
        "missing": [],
        "questions": [],
        "truncated": False,
        "warnings": [],
    },
)
KNOWN = {
    "product": "DEMO-P03",
    "quantity": "100",
    "unit": "cuộn",
    "requested_delivery_date": "2026-10-20",
}


def message(task="rank_suppliers", **extra):
    return {
        "task": task,
        "question": "100 cuộn DEMO-P03 ngày mốt giao",
        "business_date": "2026-10-18",
        "known": KNOWN.copy(),
        "evidence": [],
        **extra,
    }


def adapter(original, caller="prediction", target="database"):
    return A2AProtocolAdapter(
        source_tools=[original], caller_service=caller, target_service=target
    ).build_tools()[0]


def call(tool, value):
    return ToolCallRequest(
        tool_call={
            "name": tool.name,
            "args": {"input_value": json.dumps(value)},
            "id": "offline",
            "type": "tool_call",
        },
        tool=tool,
        state={},
        runtime=None,
    )


@pytest.mark.parametrize("wrong_task", ["rank_suppliers", "offers", "resolve_entities"])
def test_captured_bad_dependency_is_canonical_and_uses_original_runtime_receipt(wrong_task):
    with invocation("prediction") as scope:
        scope.request = Request.model_validate(message())
        collect(scope, ENTITY)
        bad = message(
            wrong_task,
            known={"product_id": "invented-id", "quantity": "999"},
            evidence=[{"tool": "invented", "data": {"risk_score": 0.01}}],
        )
        normalized = normalize_request(json.dumps(bad), "database", scope)
        assert normalized.task == "resolve_entities"
        assert normalized.known == {"product": KNOWN["product"], **ENTITY["data"]["resolved"]}
        assert normalized.evidence[0].model_dump(mode="json") == ENTITY
        assert normalized.model_dump(mode="json")["business_date"] == "2026-10-18"
        assert not scope.run.a2a_calls and not scope.run.model_calls


def test_receipts_of_other_products_are_never_forwarded():
    with invocation("central") as scope:
        collect(scope, ENTITY)
        assert matching_receipts(scope, {"product": "DEMO-P19"}) == []
        normalized = normalize_request(
            json.dumps(message(known={"product": "DEMO-P19"})), "prediction", scope
        )
        assert normalized.evidence == [] and "product_id" not in normalized.known


@pytest.mark.asyncio
async def test_native_a2a_retains_receipts_while_model_sees_compact_reply_and_no_second_lookup():
    async def native(_self):
        with invocation("database") as data:
            assert data.request.task == "resolve_entities"
            collect(data, ENTITY)
            return Message(
                text=json.dumps(finish_reply({"service": "database", "status": "success"}, data))
            )

    async def original(input_value: str):
        return (
            await BoundedA2AAgent(
                mode="Internal",
                caller_service="prediction",
                target_service="database",
                input_value=input_value,
            ).send_to_agent()
        ).text

    lookup = adapter(
        StructuredTool.from_function(coroutine=original, name="hoi_du_lieu", description="offline")
    )
    rank = StructuredTool.from_function(
        coroutine=AsyncMock(),
        name="prediction_rank_suppliers",
        description="offline",
        args_schema=ProtocolInput,
        infer_schema=False,
    )
    middleware = CoordinationMiddleware()
    with invocation("prediction") as scope:
        scope.request = Request.model_validate(message())

        async def handler(request):
            return ToolMessage(
                content=json.dumps(await lookup.ainvoke(request.tool_call["args"])),
                tool_call_id="offline",
            )

        with patch.object(A2AAgentComponent, "_run_internal_agent", native):
            response = loads(
                (
                    await middleware.awrap_tool_call(
                        call(lookup, message("rank_suppliers")), handler
                    )
                ).content
            )
        assert response["status"] == "success" and response["resolved"]["product_id"] == PRODUCT
        assert response["results"] == []
        assert scope.receipts == [ENTITY] and scope.run.a2a_calls == 1
        assert scope.child_replies[0]["status"] == "success"
        capture = AsyncMock(return_value="offline")
        await middleware.awrap_model_call(
            ModelRequest(
                model=ChatOpenAI(model="offline", api_key="offline"),
                messages=[],
                tools=[lookup, rank],
            ),
            capture,
        )
        assert [tool.name for tool in capture.call_args.args[0].tools] == [
            "prediction_rank_suppliers"
        ]
        assert scope.calls == 1
    assert PENDING.get() is None


def test_verified_evidence_hides_data_even_before_the_first_child_model_call():
    original = StructuredTool.from_function(
        coroutine=AsyncMock(),
        name="hoi_du_lieu",
        description="offline",
        args_schema=ProtocolInput,
        infer_schema=False,
    )
    tool = adapter(original)
    with invocation("prediction") as scope:
        scope.request = Request.model_validate(message())
        assert tool.action == "hoi_du_lieu"
        collect(scope, ENTITY)
        assert tool.action == "dependency_completed"
    with invocation("prediction") as fresh:
        fresh.request = Request.model_validate(message())
        assert tool.action == "hoi_du_lieu"


@pytest.mark.asyncio
async def test_wrong_root_route_and_uncorrelated_replies_do_not_execute_or_claim_success():
    original = AsyncMock(
        return_value={
            "service": "database",
            "status": "success",
            "resolved": {"product_id": PRODUCT},
        }
    )
    source = StructuredTool.from_function(
        coroutine=original,
        name="hoi_du_lieu",
        description="offline",
        args_schema=ProtocolInput,
        infer_schema=False,
    )
    tool = adapter(source, caller="central")
    with invocation("central"):
        wrong = await tool.ainvoke({"input_value": json.dumps(message())})
        assert wrong["status"] == "error" and wrong["warnings"] == ["PROTOCOL_TASK_NOT_ALLOWED"]
        original.assert_not_awaited()
        uncorrelated = await tool.ainvoke({"input_value": json.dumps(message("resolve_entities"))})
        assert uncorrelated["warnings"] == ["PROTOCOL_REPLY_UNCORRELATED"]


@pytest.mark.parametrize("status", ["success", "ambiguous", "needs_input", "error", "unavailable"])
def test_native_type_convert_emits_valid_json_without_changing_receipts_or_errors(status):
    value = Reply(
        service="database",
        status=status,
        results=[ENTITY],
        resolved=ENTITY["data"]["resolved"],
        warnings=["EXISTING_ERROR"] if status == "error" else [],
    ).model_dump(mode="json")
    validated = A2AReplyEnvelope(
        service="database", input_value=Message(text="```json\n" + json.dumps(value) + "\n```")
    )
    envelope = validated.validate_reply()
    output = TypeConverterComponent(input_data=envelope, output_type="Message").convert_to_message()
    assert json.loads(output.text) == value
    assert not output.text.startswith("```")


def test_invalid_service_reply_stays_an_error_in_the_new_envelope_node():
    envelope = A2AReplyEnvelope(
        service="prediction", input_value=Message(text='{"service":"database","status":"success"}')
    )
    value = json.loads(envelope.validate_reply().data["text"])
    assert value["status"] == "error" and not value["results"]
    assert value["warnings"] == ["PROTOCOL_INVALID_REPLY_ENVELOPE"]


class ScriptedModel(FakeMessagesListChatModel):
    seen_tools: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.seen_tools.append([tool.name for tool in tools])
        return self


@pytest.mark.asyncio
async def test_native_agent_loop_rank_succeeds_after_normalizing_captured_bad_dependency():
    models = []
    ranked = tool_result(
        "prediction_rank_suppliers",
        {
            "product_id": PRODUCT,
            "quantity": "100",
            "requested_delivery_date": "2026-10-20",
        },
        {
            "result": {
                "status": "success",
                "candidates": [{"supplier_id": "offline", "risk_score": 0.2}],
            }
        },
    )

    def turn(name, args, index):
        return AIMessage(
            content="",
            tool_calls=[{"name": name, "args": args, "id": str(index), "type": "tool_call"}],
        )

    async def ask_data(input_value: str):
        return (
            await BoundedA2AAgent(
                mode="Internal",
                caller_service="prediction",
                target_service="database",
                input_value=input_value,
            ).send_to_agent()
        ).text

    async def ask_ml(input_value: str):
        return (
            await BoundedA2AAgent(
                mode="Internal",
                caller_service="central",
                target_service="prediction",
                input_value=input_value,
            ).send_to_agent()
        ).text

    async def rank(product_id: str, quantity: str, requested_delivery_date: str):
        assert (
            dict(
                product_id=product_id,
                quantity=quantity,
                requested_delivery_date=requested_delivery_date,
            )
            == ranked["input"]
        )
        return ranked

    async def native(self):
        with invocation(self.target_service) as scope:
            if self.target_service == "database":
                assert scope.request.task == "resolve_entities"
                collect(scope, ENTITY)
            else:
                model = ScriptedModel(
                    responses=[
                        turn(
                            "hoi_du_lieu",
                            {
                                "input_value": json.dumps(
                                    message("rank_suppliers", evidence=[{"forged": True}])
                                )
                            },
                            1,
                        ),
                        turn("prediction_rank_suppliers", ranked["input"], 2),
                        AIMessage(content="done"),
                    ]
                )
                models.append(model)
                graph = create_agent(
                    model,
                    [
                        adapter(
                            StructuredTool.from_function(
                                coroutine=ask_data, name="hoi_du_lieu", description="offline"
                            )
                        ),
                        StructuredTool.from_function(
                            coroutine=rank, name="prediction_rank_suppliers", description="offline"
                        ),
                    ],
                    middleware=[CoordinationMiddleware()],
                )
                await graph.ainvoke({"messages": [HumanMessage(content="offline")]})
            return Message(
                text=json.dumps(
                    finish_reply({"service": self.target_service, "status": "success"}, scope)
                )
            )

    root_model = ScriptedModel(
        responses=[
            turn("hoi_du_doan", {"input_value": json.dumps(message())}, 1),
            AIMessage(content="done"),
        ]
    )
    with invocation("central") as root:
        with patch.object(A2AAgentComponent, "_run_internal_agent", native):
            root_tool = adapter(
                StructuredTool.from_function(
                    coroutine=ask_ml, name="hoi_du_doan", description="offline"
                ),
                caller="central",
                target="prediction",
            )
            await create_agent(
                root_model, [root_tool], middleware=[CoordinationMiddleware()]
            ).ainvoke({"messages": [HumanMessage(content="offline")]})
        assert root.receipts == [ENTITY, ranked]
        assert root.child_replies[0]["status"] == "success"
        assert not root.child_replies[0]["warnings"]
        assert root.run.a2a_calls == 2 and root.run.model_calls == 5
    assert models[0].seen_tools[0] == ["hoi_du_lieu", "prediction_rank_suppliers"]
    assert models[0].seen_tools[1] == ["prediction_rank_suppliers"]
