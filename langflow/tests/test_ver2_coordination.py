# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt.tool_node import ToolCallRequest
from lfx.components.models_and_agents.a2a_agent import A2AAgentComponent
from lfx.components.models_and_agents.agent_helpers.single_tool_call_middleware import (
    SingleToolCallMiddleware,
)
from lfx.schema.message import Message
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.tools import tool_result  # noqa: E402
from flowbridge_agent.ver2_coordination import (  # noqa: E402
    CURRENT,
    PENDING,
    CoordinationMiddleware,
    Reply,
    Request,
    collect,
    finish_reply,
    invocation,
    loads,
)
from flowbridge_agent.ver2_coordination_nodes import (  # noqa: E402
    BoundedA2AAgent,
    CoordinatedCentralAgent,
    CoordinatedServiceAgent,
)

PRODUCT = "024b8d0d-3548-54ad-8733-8385905fdbe1"
SUPPLIER = "00000002-0002-4002-8002-000000000002"
ENTITY = tool_result(
    "catalog_resolve_entities",
    {"product": "DEMO-P03", "supplier": "NCC-06"},
    {
        "status": "success",
        "resolved": {"product_id": PRODUCT, "supplier_id": SUPPLIER, "unit": "cuộn"},
        "truncated": False,
    },
)
SCENARIO = {
    "product_id": PRODUCT,
    "supplier_id": SUPPLIER,
    "quantity": "100",
    "requested_delivery_date": "2026-10-21",
}
SCORE = tool_result(
    "prediction_score_supplier",
    SCENARIO,
    {
        "scenario": SCENARIO,
        "result": {"status": "success", "risk_score": 0.25},
        "trace": {"version": "offline"},
    },
)


def payload(task="resolve_entities", **known):
    return json.dumps(
        {
            "task": task,
            "question": "100 cuộn băng keo vàng, NCC-06, nhận 21/10/2026",
            "business_date": "2026-10-18",
            "known": known,
            "evidence": [],
        },
        ensure_ascii=False,
    )


def component(caller="prediction", target="database", text=None):
    return BoundedA2AAgent(
        mode="Internal",
        caller_service=caller,
        target_service=target,
        input_value=text or payload(product="DEMO-P03", supplier="NCC-06"),
    )


def call(name, args=None, action=None):
    return ToolCallRequest(
        tool_call={"name": name, "args": args or {}, "id": "call-" + name, "type": "tool_call"},
        tool=SimpleNamespace(action=action or name),
        state={},
        runtime=None,
    )


async def data_native(_self):
    with invocation("database") as scope:
        collect(scope, ENTITY)
        return Message(
            text=json.dumps(finish_reply({"service": "database", "status": "success"}, scope))
        )


@pytest.mark.asyncio
async def test_specialist_can_resolve_both_names_through_native_a2a_with_correlated_receipt():
    with invocation("prediction") as scope:
        with patch.object(A2AAgentComponent, "_run_internal_agent", data_native):
            result = json.loads((await component().send_to_agent()).text)
        assert result["status"] == "success"
        assert result["resolved"]["product_id"] == PRODUCT
        assert result["trace"]["path"] == ["prediction", "database"]
        assert result["trace"]["request_id"] == scope.run.identifier
        assert scope.run.a2a_calls == 1
    assert CURRENT.get() is None and PENDING.get() is None


@pytest.mark.asyncio
async def test_repeat_handoff_returns_same_evidence_without_another_agent_run():
    with invocation("prediction") as scope:
        mocked = AsyncMock(side_effect=lambda: None)
        count = []

        async def native(self):
            count.append(True)
            return await data_native(self)

        with patch.object(A2AAgentComponent, "_run_internal_agent", native):
            first = await component().send_to_agent()
            second = await component().send_to_agent()
        assert first.text == second.text and len(count) == 1
        assert scope.run.a2a_calls == 1
        mocked.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caller,target",
    [("prediction", "knowledge"), ("database", "prediction"), ("knowledge", "prediction")],
)
async def test_cycles_and_unauthorized_peer_routes_never_execute(caller, target):
    with invocation(caller):
        with patch.object(A2AAgentComponent, "_run_internal_agent", AsyncMock()) as mocked:
            result = json.loads((await component(caller, target).send_to_agent()).text)
            assert result["status"] == "error"
            mocked.assert_not_awaited()


@pytest.mark.asyncio
async def test_root_budget_is_shared_with_nested_calls_and_new_sessions_do_not_share_cache():
    ids = []
    for _ in range(2):
        with invocation("central") as root:
            ids.append(root.run.identifier)
            root.run.max_a2a = 1

            async def ml_native(_self):
                with invocation("prediction") as ml:
                    assert ml.run is root.run
                    denied = json.loads((await component().send_to_agent()).text)
                    assert denied["warnings"] == ["A2A_BUDGET_EXHAUSTED"]
                    return Message(
                        text=json.dumps(
                            finish_reply({"service": "prediction", "status": "partial"}, ml)
                        )
                    )

            with patch.object(A2AAgentComponent, "_run_internal_agent", ml_native):
                await component(
                    "central", "prediction", payload("score_supplier", **SCENARIO)
                ).send_to_agent()
            assert root.run.a2a_calls == 1
    assert ids[0] != ids[1] and CURRENT.get() is None


@pytest.mark.asyncio
async def test_model_cannot_invent_evidence_to_pass_to_another_agent():
    text = json.loads(payload())
    text["evidence"] = [ENTITY]
    with invocation("prediction"):
        with patch.object(A2AAgentComponent, "_run_internal_agent", AsyncMock()) as mocked:
            result = json.loads((await component(text=json.dumps(text)).send_to_agent()).text)
            assert result["warnings"] == ["INVALID_HANDOFF_REQUEST"]
            mocked.assert_not_awaited()


CAPTURED_REQUEST = {
    "task": "rank_suppliers",
    "question": "Xếp hạng các nhà cung cấp tiềm năng cho băng keo: "
    "100 cuộn, cần nhận hàng ngày mốt.",
    "business_date": "2026-10-18",
    "known": {
        "product": "băng keo",
        "supplier": None,
        "quantity": "100",
        "unit": "cuộn",
        "requested_delivery_date": "2026-10-20",
        "top_k": None,
        "ranking_policy": "risk_first",
        "currency": None,
        "max_unit_price": None,
    },
    "evidence": None,
}


def test_captured_request_normalizes_empty_evidence_without_changing_purchase_conditions():
    parsed = Request.model_validate(CAPTURED_REQUEST)
    assert parsed.evidence == []
    assert parsed.known == CAPTURED_REQUEST["known"]
    assert parsed.model_dump(mode="json")["business_date"] == "2026-10-18"
    assert Request.model_validate({**CAPTURED_REQUEST, "known": None}).known == {}


@pytest.mark.parametrize(
    "field,value",
    [("evidence", {}), ("evidence", "[]"), ("known", []), ("task", None), ("business_date", None)],
)
def test_null_tolerance_does_not_accept_wrong_types_or_invent_required_fields(field, value):
    with pytest.raises(ValidationError):
        Request.model_validate({**CAPTURED_REQUEST, field: value})


@pytest.mark.asyncio
async def test_captured_request_reaches_ml_then_data_and_returns_product_choices():
    choices = tool_result(
        "catalog_resolve_entities",
        {"product": "băng keo"},
        {
            "status": "ambiguous",
            "resolved": {},
            "entities": {
                "product": {
                    "items": [
                        {"product_code": "DEMO-P03", "product_name": "Băng keo vàng"},
                        {"product_code": "DEMO-P19", "product_name": "Băng keo trong"},
                    ]
                }
            },
        },
    )
    reached = []

    async def native(self):
        role = self.target_service
        with invocation(role) as scope:
            reached.append(role)
            if role == "database":
                collect(scope, choices)
                return Message(
                    text=json.dumps(
                        finish_reply(
                            {
                                "service": "database",
                                "status": "ambiguous",
                                "missing": ["product"],
                                "questions": ["Bạn chọn băng keo vàng hay trong?"],
                                "results": None,
                                "warnings": None,
                                "handoffs": None,
                            },
                            scope,
                        )
                    )
                )
            assert scope.request.evidence == []
            assert scope.request.known == CAPTURED_REQUEST["known"]
            request = {**CAPTURED_REQUEST, "task": "resolve_entities"}

            async def handler(_request):
                reply = await component(text=json.dumps(request)).send_to_agent()
                return ToolMessage(content=reply.text, tool_call_id="call-hoi_du_lieu")

            await CoordinationMiddleware().awrap_tool_call(call("hoi_du_lieu"), handler)
            return Message(
                text=json.dumps(
                    finish_reply({"service": "prediction", "status": "ambiguous"}, scope)
                )
            )

    with invocation("central"):
        with patch.object(A2AAgentComponent, "_run_internal_agent", native):
            result = json.loads(
                (
                    await component(
                        "central", "prediction", json.dumps(CAPTURED_REQUEST)
                    ).send_to_agent()
                ).text
            )
    assert reached == ["prediction", "database"]
    assert result["status"] == "ambiguous"
    assert result["questions"] == ["Bạn chọn băng keo vàng hay trong?"]
    assert not result["warnings"]
    assert "product_id" not in result["resolved"]


@pytest.mark.asyncio
async def test_bad_handoff_is_an_argument_error_not_service_unavailability():
    with invocation("central"):
        with patch.object(A2AAgentComponent, "_run_internal_agent", AsyncMock()) as native:
            result = json.loads(
                (await component("central", "prediction", '{"task":null}').send_to_agent()).text
            )
    assert result["status"] == "error"
    assert result["warnings"] == ["INVALID_HANDOFF_REQUEST"]
    native.assert_not_awaited()


def test_output_recovers_missing_uuid_from_actual_receipts_and_rejects_invented_predictions():
    with invocation("database") as scope:
        collect(scope, ENTITY)
        result = finish_reply(
            {"service": "database", "status": "success", "resolved": {}, "results": []}, scope
        )
        assert result["resolved"]["product_id"] == PRODUCT
        assert result["results"] == [ENTITY]
        forged = finish_reply(
            {"service": "database", "status": "success", "results": [SCORE]}, scope
        )
        assert forged["status"] == "error"
        assert forged["results"] == [ENTITY] and "UNSUPPORTED_TOOL_RESULT" in forged["warnings"]


def test_resolving_ids_alone_is_not_successful_prediction_and_wrong_service_is_rejected():
    with invocation("prediction") as scope:
        collect(scope, ENTITY)
        result = finish_reply({"service": "prediction", "status": "success"}, scope)
        assert result["status"] == "partial"
        wrong = finish_reply({"service": "database", "status": "success"}, scope)
        assert wrong["status"] == "error"


@pytest.mark.asyncio
async def test_ambiguous_dependency_retains_choices_and_does_not_fabricate_uuid():
    ambiguous = tool_result(
        "catalog_resolve_entities",
        {"product": "băng keo"},
        {
            "status": "ambiguous",
            "resolved": {},
            "entities": {
                "product": {
                    "items": [
                        {"product_code": "DEMO-P03", "product_name": "vàng"},
                        {"product_code": "DEMO-P19", "product_name": "trong"},
                    ]
                }
            },
        },
    )
    reply = Reply(
        service="database",
        status="ambiguous",
        results=[ambiguous],
        missing=["product"],
        questions=["Bạn chọn màu vàng hay trong?"],
    ).model_dump(mode="json")
    with invocation("prediction") as scope:
        middleware = CoordinationMiddleware()

        async def handler(_request):
            return ToolMessage(content=json.dumps(reply), tool_call_id="call-hoi_du_lieu")

        await middleware.awrap_tool_call(call("hoi_du_lieu"), handler)
        final = finish_reply({"service": "prediction", "status": "success"}, scope)
        assert final["status"] == "ambiguous" and "product_id" not in final["resolved"]
        assert final["questions"] == ["Bạn chọn màu vàng hay trong?"]


@pytest.mark.asyncio
async def test_tool_middleware_deduplicates_reuses_actual_results_and_limits_api_calls():
    with invocation("prediction") as scope:
        handler = AsyncMock(return_value=ToolMessage(content=json.dumps(SCORE), tool_call_id="x"))
        middleware = CoordinationMiddleware()
        for _ in range(2):
            result = await middleware.awrap_tool_call(
                call("prediction_score_supplier", SCENARIO), handler
            )
            assert json.loads(result.content) == SCORE
        assert handler.await_count == 1 and len(scope.receipts) == 1
        other = call("prediction_score_supplier", {**SCENARIO, "quantity": "200"})
        denied = await middleware.awrap_tool_call(other, handler)
        assert json.loads(denied.content)["error"]["code"] == "API_BUDGET_EXHAUSTED"
        assert handler.await_count == 1


@pytest.mark.asyncio
async def test_model_global_budget_and_openai_parallel_setting_without_network():
    model = ChatOpenAI(model="offline", api_key="offline-placeholder")
    request = ModelRequest(model=model, messages=[], tools=[{}])
    handler = AsyncMock(return_value="ok")
    with invocation("central") as scope:
        scope.run.max_models = 1
        middleware = CoordinationMiddleware()
        await middleware.awrap_model_call(request, handler)
        assert handler.call_args.args[0].model_settings["parallel_tool_calls"] is False
        with pytest.raises(ValueError, match="MODEL_BUDGET_EXHAUSTED"):
            await middleware.awrap_model_call(request, handler)
        assert handler.await_count == 1


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def turn(name, args, index):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": str(index), "type": "tool_call"}]
    )


@pytest.mark.asyncio
async def test_native_agent_loop_central_to_ml_to_data_then_ml_then_central():
    # A scripted model tests native tool-loop plumbing, not model reasoning accuracy.
    observed = []

    async def run_native(role, responses, tools):
        with invocation(role) as scope:
            graph = create_agent(
                OfflineModel(responses=responses),
                tools,
                middleware=[
                    ModelCallLimitMiddleware(run_limit=4),
                    CoordinationMiddleware(),
                    SingleToolCallMiddleware(),
                ],
            )
            await graph.ainvoke({"messages": [HumanMessage(content="offline test")]})
            if role == "central":
                observed.extend(scope.receipts)
                return scope.run
            return Message(
                text=json.dumps(finish_reply({"service": role, "status": "success"}, scope))
            )

    async def lookup(product: str, supplier: str):
        assert product == "DEMO-P03" and supplier == "NCC-06"
        return ENTITY

    async def score(product_id: str, supplier_id: str, quantity: str, requested_delivery_date: str):
        assert (
            dict(
                product_id=product_id,
                supplier_id=supplier_id,
                quantity=quantity,
                requested_delivery_date=requested_delivery_date,
            )
            == SCENARIO
        )
        return SCORE

    async def ask_data(input_value: str):
        return (await component(text=input_value).send_to_agent()).text

    async def ask_ml(input_value: str):
        return (await component("central", "prediction", input_value).send_to_agent()).text

    async def native(self):
        if self.target_service == "database":
            return await run_native(
                "database",
                [turn("catalog_resolve_entities", ENTITY["input"], 1), AIMessage(content="done")],
                [
                    StructuredTool.from_function(
                        coroutine=lookup, name="catalog_resolve_entities", description="offline"
                    )
                ],
            )
        return await run_native(
            "prediction",
            [
                turn(
                    "hoi_du_lieu",
                    {"input_value": payload(product="DEMO-P03", supplier="NCC-06")},
                    1,
                ),
                turn("prediction_score_supplier", SCENARIO, 2),
                AIMessage(content="done"),
            ],
            [
                StructuredTool.from_function(
                    coroutine=ask_data, name="hoi_du_lieu", description="offline"
                ),
                StructuredTool.from_function(
                    coroutine=score, name="prediction_score_supplier", description="offline"
                ),
            ],
        )

    with patch.object(A2AAgentComponent, "_run_internal_agent", native):
        run = await run_native(
            "central",
            [
                turn(
                    "hoi_du_doan",
                    {
                        "input_value": payload(
                            "score_supplier",
                            product="DEMO-P03",
                            supplier="NCC-06",
                            quantity="100",
                            requested_delivery_date="2026-10-21",
                        )
                    },
                    1,
                ),
                AIMessage(content="done"),
            ],
            [
                StructuredTool.from_function(
                    coroutine=ask_ml, name="hoi_du_doan", description="offline"
                )
            ],
        )
    assert run.a2a_calls == 2 and run.model_calls == 7
    assert [r["tool"] for r in observed] == [
        "catalog_resolve_entities",
        "prediction_score_supplier",
    ]
    assert CURRENT.get() is None


@pytest.mark.asyncio
async def test_unavailable_rag_does_not_spend_a_model_or_call_data():
    agent = CoordinatedServiceAgent(coordination_role="knowledge", tools=[])
    with patch(
        "flowbridge_agent.ver2_structured.StructuredServiceAgent.json_response", AsyncMock()
    ) as mocked:
        result = await agent.json_response()
    assert result.data["status"] == "unavailable"
    mocked.assert_not_awaited()


def test_agent_keeps_native_budget_middleware_and_adds_governor_without_retries():
    for agent in [CoordinatedCentralAgent(), CoordinatedServiceAgent()]:
        names = [type(m).__name__ for m in agent._build_middleware(object())]
        assert "ModelCallLimitMiddleware" in names and "CoordinationMiddleware" in names
        assert "ToolRetryMiddleware" not in names and "SingleToolCallMiddleware" in names


@pytest.mark.asyncio
async def test_concurrent_roots_and_native_sub_session_runner_keep_separate_budgets():
    async def root():
        with invocation("prediction") as scope:
            with patch.object(A2AAgentComponent, "_run_internal_agent", data_native):
                result = json.loads((await component().send_to_agent()).text)
            await asyncio.sleep(0)
            assert result["trace"]["request_id"] == scope.run.identifier
            return scope.run.identifier

    first, second = await asyncio.gather(root(), root())
    assert first != second and CURRENT.get() is None


@pytest.mark.asyncio
async def test_native_toolkit_deepcopy_retains_configured_caller_and_target():
    item = component()
    toolkit = await item.to_toolkit()
    assert len(toolkit) == 1
    assert toolkit[0].name == "hoi_du_lieu"
    with invocation("prediction"):
        with patch.object(A2AAgentComponent, "_run_internal_agent", data_native):
            result = await toolkit[0].ainvoke(
                {"input_value": payload(product="DEMO-P03", supplier="NCC-06")}
            )
    assert json.loads(result)["resolved"]["product_id"] == PRODUCT


@pytest.mark.asyncio
async def test_graph_upgrade_adds_only_required_native_edges_and_preserves_credentials():
    from uuid import NAMESPACE_URL, uuid5

    from setup_ver2 import ENDPOINTS, specialist_flow
    from upgrade_ver2_coordination import coordinated_graph

    target = {
        "database": {
            "name": "FlowBridge - ver2 - Dữ liệu",
            "id": str(uuid5(NAMESPACE_URL, ENDPOINTS["database"])),
        }
    }
    for role, count in [("database", 11), ("prediction", 11), ("knowledge", 8)]:
        original = specialist_flow(role)
        before = json.loads(json.dumps(original))
        after = await coordinated_graph(original, role, target)
        assert original == before and len(after["nodes"]) == count
        assert len(after["edges"]) == count - 1
        for old in original["data"]["nodes"]:
            actual = next(n for n in after["nodes"] if n["id"] == old["id"])
            fields = old["data"]["node"]["template"]
            for key in {"api_origin", "access_token", "api_key", "model"} & fields.keys():
                assert actual["data"]["node"]["template"][key] == fields[key]
        updated = {**original, "data": after}
        assert await coordinated_graph(updated, role, target) == after


@pytest.mark.asyncio
async def test_central_migration_recovers_native_action_names_from_selected_flow_ids():
    from upgrade_ver2_coordination import coordinated_graph

    flow = json.loads((ROOT / "flows/ver2/central.json").read_text(encoding="utf-8"))
    slugs = {"hoi_du_lieu": "database", "hoi_du_doan": "prediction", "hoi_tri_thuc": "knowledge"}
    targets = {}
    for item in flow["data"]["nodes"]:
        if item["data"]["type"] == "A2AAgent":
            fields = item["data"]["node"]["template"]
            action = fields["tools_metadata"]["value"][0]
            role = slugs[action["name"]]
            targets[role] = {
                "id": fields["agent_name_selected"]["options_metadata"][0]["id"],
                "name": fields["agent_name_selected"]["value"],
            }
            action["name"] = "send_to_agent"
    data = await coordinated_graph(flow, "central", targets)
    actions = [
        n["data"]["node"]["template"]["tools_metadata"]["value"][0]["name"]
        for n in data["nodes"]
        if n["data"]["type"] == "A2AAgent"
    ]
    assert set(actions) == set(slugs)


def test_native_parser_transport_is_decoded_without_extracting_json_from_prose():
    from lfx.components.processing.parser import ParserComponent
    from lfx.schema.data import Data

    data = {"service": "database", "status": "ambiguous", "questions": ["Chọn A hay B?"]}
    parser = ParserComponent(mode="Stringify", input_data=Data(data=data))
    assert loads(parser.convert_to_string().text) == data
    assert loads("```\n" + json.dumps(data) + "\n```") == data
    for invalid in ("Lời dẫn\n" + json.dumps(data), json.dumps(data) + "\nextra", "[]"):
        with pytest.raises(ValueError):
            loads(invalid)


@pytest.mark.asyncio
async def test_fenced_native_reply_keeps_real_ambiguity_and_correlation():
    async def native(self):
        result = await data_native(self)
        result.text = "```json\n" + result.text + "\n```"
        return result

    with invocation("prediction"):
        with patch.object(A2AAgentComponent, "_run_internal_agent", native):
            result = json.loads((await component().send_to_agent()).text)
        assert result["status"] == "success"
        assert result["resolved"]["product_id"] == PRODUCT


@pytest.mark.asyncio
async def test_child_model_sees_only_apis_for_the_requested_task():
    tools = [
        StructuredTool.from_function(
            func=lambda: "unused",
            name=name,
            description="offline",
        )
        for name in ("catalog_resolve_entities", "catalog_read_offers", "purchase_get_order")
    ]
    request = ModelRequest(
        model=ChatOpenAI(model="gpt-5.6-luna", api_key="offline-only"),
        messages=[HumanMessage(content="offline")],
        tools=tools,
        state={"messages": []},
        runtime=None,
    )
    with invocation("central"):
        token = PENDING.set(("database", Request.model_validate(json.loads(payload(product="P")))))
        try:
            with invocation("database"):

                async def handler(filtered):
                    assert [t.name for t in filtered.tools] == ["catalog_resolve_entities"]
                    return "checked"

                assert (
                    await CoordinationMiddleware().awrap_model_call(request, handler) == "checked"
                )
                blocked_handler = AsyncMock()
                result = await CoordinationMiddleware().awrap_tool_call(
                    call("catalog_read_offers"),
                    blocked_handler,
                )
                assert json.loads(result.content)["error"]["code"] == "TOOL_OUTSIDE_TASK"
                blocked_handler.assert_not_awaited()
        finally:
            PENDING.reset(token)


@pytest.mark.asyncio
async def test_migration_removes_reset_native_cleaner_with_ui_component_alias():
    from lfx.components.processing.operations import OperationsComponent
    from setup_ver2 import connect, native, specialist_flow
    from upgrade_ver2_coordination import coordinated_graph

    flow = specialist_flow("database")
    data = flow["data"]
    parser = next(n for n in data["nodes"] if n["data"]["type"] == "Parser")
    parser["data"]["type"] = "ParserComponent"
    output = next(n for n in data["nodes"] if n["data"]["type"] == "ChatOutput")
    cleaner = native(
        OperationsComponent,
        "lfx.components.processing.operations",
        "Operations",
        "database",
        "Data Operations",
        0,
        0,
    )
    data["nodes"].append(cleaner)
    data["edges"] = [e for e in data["edges"] if e["target"] != output["id"]]
    data["edges"].extend(
        [
            connect(parser, "parsed_text", cleaner, "text_input"),
            connect(cleaner, "message_output", output, "input_value"),
        ]
    )
    upgraded = await coordinated_graph(flow, "database", {})
    assert len(upgraded["nodes"]) == 11 and len(upgraded["edges"]) == 10
    assert all(n["data"]["type"] != "Operations" for n in upgraded["nodes"])
    assert any(
        e["source"] == parser["id"] and e["target"] == output["id"] for e in upgraded["edges"]
    )
