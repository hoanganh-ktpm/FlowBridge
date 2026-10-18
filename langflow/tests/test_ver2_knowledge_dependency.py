# SPDX-License-Identifier: GPL-3.0-or-later
"""RAG identity lookup must finish from genuine receipts before retrieval can run."""

import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from lfx.components.models_and_agents.a2a_agent import A2AAgentComponent
from lfx.schema.message import Message

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.tools import ToolResult, tool_result  # noqa: E402
from flowbridge_agent.ver2_coordination import (  # noqa: E402
    CoordinationMiddleware,
    Invocation,
    Reply,
    Request,
    Run,
    collect,
    finish_reply,
    invocation,
)
from flowbridge_agent.ver2_coordination_nodes import BoundedA2AAgent  # noqa: E402
from flowbridge_agent.ver2_entity_dependency import complete_entity_dependency  # noqa: E402
from flowbridge_agent.ver2_protocol_nodes import (  # noqa: E402
    A2AProtocolAdapter,
    normalize_request,
)

SUPPLIER = "00000000-0000-4000-8000-000000000003"
ITEM = {"supplier_id": SUPPLIER, "supplier_code": "DEMO-S03", "supplier_name": "NCC-03"}
ENTITY = tool_result(
    "catalog_resolve_entities",
    {"supplier": "NCC-03"},
    {
        "status": "success",
        "truncated": False,
        "resolved": ITEM,
        "missing": [],
        "questions": [],
        "warnings": [],
        "entities": {
            "supplier": {
                "status": "success",
                "truncated": False,
                "match_count": 1,
                "query": "NCC-03",
                "items": [ITEM],
            }
        },
    },
)
NARRATIVE = "Đã xác định duy nhất NCC-03; tài liệu giao hàng thuộc nhiệm vụ của RAG."


def parent():
    return Request.model_validate(
        {
            "task": "retrieve",
            "question": "Đánh giá giao hàng NCC-03, nêu đơn hàng và dẫn nguồn.",
            "business_date": "2026-10-18",
            "known": {"supplier": "NCC-03", "query": "NCC-03 tiến độ giao hàng"},
        }
    )


def scenario():
    scope = Invocation(Run(), "knowledge", ("central", "knowledge"), parent())
    request = normalize_request(json.dumps(parent().model_dump(mode="json")), "database", scope)
    collect(scope, ENTITY)
    reply = Reply.model_validate(
        {
            "service": "database",
            "status": "error",
            "results": [ENTITY],
            "warnings": [NARRATIVE, "INVALID_DEPENDENCY"],
            "trace": {"request_id": scope.run.identifier, "path": [*scope.path, "database"]},
        }
    )
    return scope, request, reply


def test_rag_dependency_gets_only_supplier_identity_and_preserves_original_retrieval():
    scope, request, reply = scenario()
    assert request.task == "resolve_entities"
    assert request.known == {"supplier": "NCC-03"}
    assert request.question != parent().question
    assert scope.request == parent()
    result = complete_entity_dependency(reply, request, scope)
    assert result.status == "success" and result.resolved == ITEM
    assert result.warnings == []
    assert result.trace["ignored_out_of_scope_warnings"] == reply.warnings
    assert reply.status == "error"  # No mutation of the original reply.
    assert scope.run.events[-1]["source"] == "knowledge"
    assert not scope.run.model_calls and not scope.run.a2a_calls


@pytest.mark.parametrize(
    "failure",
    [
        "unobserved",
        "wrong_run",
        "wrong_path",
        "forbidden",
        "schema_failure",
        "questions",
        "missing",
        "ambiguous",
        "truncated",
        "wrong_uuid",
        "wrong_supplier",
        "no_receipt",
        "api_error",
        "wrong_task",
    ],
)
def test_rag_dependency_never_clears_real_failures_or_unverified_identity(failure):
    scope, request, reply = scenario()
    receipt = deepcopy(ENTITY)
    if failure == "unobserved":
        scope.run.evidence.clear()
    elif failure == "wrong_run":
        reply.trace["request_id"] = "other"
    elif failure == "wrong_path":
        reply.trace["path"] = ["central", "database"]
    elif failure == "forbidden":
        reply.warnings.append("FORBIDDEN")
    elif failure == "schema_failure":
        reply.warnings = ["SERVICE_OUTPUT_INVALID"]
    elif failure == "questions":
        reply.questions = ["Chọn nhà cung cấp nào?"]
    elif failure == "missing":
        reply.missing = ["supplier"]
    elif failure == "ambiguous":
        receipt["data"]["entities"]["supplier"]["match_count"] = 2
    elif failure == "truncated":
        receipt["data"]["truncated"] = True
    elif failure == "wrong_uuid":
        request.known["supplier_id"] = "00000000-0000-4000-8000-000000000004"
    elif failure == "wrong_supplier":
        request.known["supplier"] = "NCC-04"
    elif failure == "no_receipt":
        reply.results = []
    elif failure == "api_error":
        receipt["status"] = "error"
        receipt["data"] = None
        receipt["error"] = {"code": "FORBIDDEN", "http_status": 403, "retryable": False}
    elif failure == "wrong_task":
        scope.request.task = "order_risk"
    if receipt != ENTITY:
        # Even an observed API failure/incomplete response cannot establish identity.
        collect(scope, receipt)
        reply.results = [ToolResult.model_validate(receipt)]
    result = complete_entity_dependency(reply, request, scope)
    assert result is reply and result.status == "error"


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.asyncio
async def test_native_rag_loop_continues_after_captured_invalid_dependency():
    async def native(_self):
        with invocation("database") as data:
            assert data.request.known == {"supplier": "NCC-03"}
            collect(data, ENTITY)
            # Reproduce Data attempting the parent's retrieval task after resolving identity.
            raw = {
                "service": "database",
                "status": "needs_service",
                "warnings": [NARRATIVE],
                "handoffs": [{"service": "database", "task": "retrieve", "reason": "Tìm tài liệu"}],
            }
            failed = finish_reply(raw, data)
            assert "INVALID_DEPENDENCY" in failed["warnings"]
            return Message(text=json.dumps(failed))

    async def lookup(input_value: str):
        return (
            await BoundedA2AAgent(
                mode="Internal",
                caller_service="knowledge",
                target_service="database",
                input_value=input_value,
            ).send_to_agent()
        ).text

    retrieve = AsyncMock(
        return_value=tool_result(
            "knowledge_search_supplier",
            {
                "supplier_id": SUPPLIER,
                "query": parent().known["query"],
                "top_k": 3,
            },
            {"chunks": [], "warnings": []},
        )
    )

    async def search(supplier_id: str, query: str, top_k: int):
        return await retrieve(supplier_id=supplier_id, query=query, top_k=top_k)

    def turn(name, args, index):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": name,
                    "args": args,
                    "id": str(index),
                    "type": "tool_call",
                }
            ],
        )

    model = ScriptedModel(
        responses=[
            turn("hoi_du_lieu", {"input_value": json.dumps(parent().model_dump(mode="json"))}, 1),
            turn(
                "knowledge_search_supplier",
                {
                    "supplier_id": SUPPLIER,
                    "query": parent().known["query"],
                    "top_k": 3,
                },
                2,
            ),
            AIMessage(content="done"),
        ]
    )
    dependency = A2AProtocolAdapter(
        source_tools=[
            StructuredTool.from_function(
                coroutine=lookup,
                name="hoi_du_lieu",
                description="offline",
            )
        ],
        caller_service="knowledge",
        target_service="database",
    ).build_tools()[0]
    with invocation("knowledge") as scope:
        scope.request = parent()
        with patch.object(A2AAgentComponent, "_run_internal_agent", native):
            await create_agent(
                model,
                [
                    dependency,
                    StructuredTool.from_function(
                        coroutine=search,
                        name="knowledge_search_supplier",
                        description="offline",
                    ),
                ],
                middleware=[CoordinationMiddleware()],
            ).ainvoke(
                {
                    "messages": [HumanMessage(content="offline")],
                }
            )
        result = finish_reply({"service": "knowledge", "status": "success"}, scope)
        assert result["status"] == "success" and result["warnings"] == []
        assert scope.child_replies[0]["status"] == "success"
        assert scope.run.a2a_calls == 1 and scope.run.model_calls == 3
        assert scope.api_calls == 1
        retrieve.assert_awaited_once_with(
            supplier_id=SUPPLIER,
            query=parent().known["query"],
            top_k=3,
        )
