# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from lfx.schema.data import Data
from lfx.schema.message import Message
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.config import BackendConfig  # noqa: E402
from flowbridge_agent.tools import configured_tool, failure, tool_result  # noqa: E402
from flowbridge_agent.ver2_data import DataBackend  # noqa: E402
from flowbridge_agent.ver2_data_nodes import DataActionTool  # noqa: E402
from flowbridge_agent.ver3_contracts import (  # noqa: E402
    ROUTES,
    Decision,
    Intent,
    ServiceRequest,
    Slots,
    normalize_slots,
)
from flowbridge_agent.ver3_nodes import (  # noqa: E402
    AnswerRenderer,
    RequestNormalizer,
    ServiceBridge,
    payload,
)
from flowbridge_agent.ver3_state import StateStore  # noqa: E402
from flowbridge_agent.ver3_workflow import (  # noqa: E402
    BOUNDARY,
    Boundary,
    Workflow,
    fast_reply,
    merge,
    new_state,
    render,
)
from setup_ver3 import cloned, replace_values, upgrade  # noqa: E402

PRODUCT = "91fcd2be-acf6-5b51-948f-b3a7f537ed55"
OTHER = "024b8d0d-3548-54ad-8733-8385905fdbe1"
SUPPLIER = "00000002-0002-4002-8002-000000000002"
DAY = "2026-10-18"
PAPER = {
    "product_id": PRODUCT,
    "product_code": "DEMO-P07",
    "product_name": "Giấy A4",
    "unit": "ram",
    "product_category": "office",
}
TAPES = [
    {
        "product_id": OTHER,
        "product_code": "DEMO-P19",
        "product_name": "Băng keo vàng",
        "unit": "cuộn",
    },
    {
        "product_id": PRODUCT,
        "product_code": "DEMO-P20",
        "product_name": "Băng keo trong",
        "unit": "cuộn",
    },
]


def decision(mode="update", tasks=None, selection=None, clear=None, **slots):
    return Decision(
        mode=mode,
        tasks=tasks or [],
        selection=selection,
        clear=clear or [],
        reply=None,
        slots=Slots(**{k: slots.get(k) for k in Slots.model_fields}),
    )


def resolved(arguments, product=None):
    values, entities = {}, {}
    for kind in ("product", "supplier"):
        if kind in arguments:
            row = (
                (product or PAPER)
                if kind == "product"
                else {
                    "supplier_id": SUPPLIER,
                    "supplier_name": "Phúc Lâm",
                    "supplier_code": "NCC-06",
                }
            )
            values.update(row)
            entities[kind] = {"status": "success", "items": [row], "truncated": False}
    return {"status": "success", "resolved": values, "entities": entities, "truncated": False}


class Backend:
    def __init__(self, *, ambiguous=False, fail=False):
        self.calls = []
        self.ambiguous, self.fail = ambiguous, fail

    async def __call__(self, request):
        self.calls.append(request)
        action = ROUTES[request.task][1]
        args = request.arguments
        if self.fail:
            return tool_result(action, args, failure("BACKEND_UNAVAILABLE"))
        if request.task == Intent.RESOLVE:
            if self.ambiguous and args.get("product") == "băng keo":
                data = {
                    "status": "ambiguous",
                    "resolved": {},
                    "entities": {
                        "product": {"status": "ambiguous", "items": TAPES, "truncated": False}
                    },
                    "questions": ["Chọn loại băng keo"],
                    "truncated": False,
                }
            else:
                data = resolved(args, TAPES[0] if args.get("product") == "DEMO-P19" else None)
        elif request.task == Intent.RANK:
            data = {
                "status": "success",
                "candidates": [
                    {
                        "supplier_id": SUPPLIER,
                        "supplier_name": "Phúc Lâm",
                        "unit_price": "50000",
                        "currency": "VND",
                        "rank": 1,
                        "result": {"status": "success", "risk_score": 0.25},
                    }
                ],
            }
        elif request.task == Intent.SCORE:
            data = {
                "result": {
                    "status": "insufficient_data",
                    "risk_score": None,
                    "missing_fields": ["q_ref"],
                }
            }
        else:
            data = {"status": "success", "items": []}
        return tool_result(action, args, data)


def run(previous, parsed, backend=None, day=DAY):
    backend = backend or Backend()
    output = asyncio.run(Workflow(backend).run(previous, parsed, "câu hỏi", day))
    return output, backend


def test_fast_gate_matches_whole_message_only():
    assert fast_reply("Bạn là ai?")
    assert fast_reply("bạn khỏe không!")
    assert fast_reply("Chào bạn, tìm giúp NCC giấy") is None
    assert fast_reply("Bạn là ai, và giá giấy hôm nay?") is None


def test_native_a2a_message_is_decoded_from_text_before_data_base_class():
    receipt = tool_result("catalog_resolve_entities", {}, {"status": "success"})
    message = Message(text=json.dumps(receipt))
    assert isinstance(message, Data)
    assert payload(message) == receipt


def test_contract_has_no_model_evidence_or_uuid_and_rejects_free_tasks():
    value = decision().model_dump()
    with pytest.raises(ValidationError):
        Decision.model_validate({**value, "evidence": []})
    with pytest.raises(ValidationError):
        Decision.model_validate({**value, "tasks": ["read_offers"]})
    assert not any(k.endswith("_id") for k in Slots.model_fields)
    schema = Decision.model_json_schema()
    for definition in [schema, *schema["$defs"].values()]:
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])


@pytest.mark.parametrize(
    "slot",
    [
        {"quantity": "NaN"},
        {"quantity": "0"},
        {"quantity": "1,000,000"},
        {"quantity": "-3"},
        {"delivery_date": "2026-10-17"},
        {"top_k": 21},
        {"relative_days": 2, "delivery_date": "2026-10-20"},
    ],
)
def test_normalizer_rejects_bad_or_conflicting_values(slot):
    with pytest.raises(ValueError):
        normalize_slots(decision(**slot).slots, DAY)


def test_ranking_resolves_once_then_calls_ml_without_handoff_or_offer_call():
    parsed = decision(
        mode="new",
        tasks=[Intent.RANK],
        product="DEMO-P07",
        quantity="300",
        unit="ram",
        relative_days=2,
    )
    output, backend = run({}, parsed)
    assert [r.task for r in backend.calls] == [Intent.RESOLVE, Intent.RANK]
    assert backend.calls[1].arguments == {
        "product_id": PRODUCT,
        "quantity": "300",
        "requested_delivery_date": "2026-10-20",
    }
    assert all("evidence" not in r.model_dump() for r in backend.calls)
    assert output["state"]["verified"]["product_id"] == PRODUCT
    assert all(
        e["task_id"] == output["state"]["task_id"] for e in output["state"]["evidence"].values()
    )
    assert "0.25" in render(output)


def test_missing_inputs_reuse_verified_identity_next_day_without_sliding_date():
    first, backend = run(
        {}, decision(mode="new", tasks=[Intent.RANK], product="DEMO-P07", relative_days=2)
    )
    assert first["status"] == "needs_input" and len(backend.calls) == 1
    second, backend = run(first["state"], decision(quantity="300", unit="ram"), day="2026-10-19")
    assert [r.task for r in backend.calls] == [Intent.RANK]
    assert backend.calls[0].arguments["requested_delivery_date"] == "2026-10-20"


def test_ambiguous_followup_keeps_constraints_without_resolving_again():
    backend = Backend(ambiguous=True)
    first, _ = run({}, decision(mode="new", tasks=[Intent.RANK], product="băng keo"), backend)
    assert first["status"] == "needs_choice"
    before = len(backend.calls)
    second, _ = run(first["state"], decision(quantity="100", unit="cuộn", relative_days=2), backend)
    assert len(backend.calls) == before and second["status"] == "needs_choice"
    third, _ = run(second["state"], decision(selection="loại vàng"), backend)
    assert third["status"] == "completed"
    assert backend.calls[-1].arguments["product_id"] == OTHER
    assert backend.calls[-1].arguments["quantity"] == "100"


def test_change_product_invalidates_uuid_and_never_converts_units():
    first, _ = run(
        {},
        decision(
            mode="new",
            tasks=[Intent.RANK],
            product="DEMO-P07",
            quantity="300",
            unit="ram",
            relative_days=2,
        ),
    )
    changed = merge(first["state"], decision(product="DEMO-P19"), DAY)
    assert "product_id" not in changed["verified"] and not changed["evidence"]
    second, backend = run(first["state"], decision(product="DEMO-P19"))
    assert second["status"] == "needs_input"
    assert [r.task for r in backend.calls] == [Intent.RESOLVE]


def test_supplier_followup_retains_product_quantity_date_and_uses_scenario():
    first, _ = run(
        {},
        decision(
            mode="new",
            tasks=[Intent.RANK],
            product="DEMO-P07",
            quantity="300",
            unit="ram",
            relative_days=2,
        ),
    )
    second, backend = run(first["state"], decision(supplier="NCC-06"))
    assert [r.task for r in backend.calls] == [Intent.RESOLVE, Intent.SCORE]
    assert backend.calls[0].arguments == {"supplier": "NCC-06"}
    assert backend.calls[1].arguments["product_id"] == PRODUCT
    assert "insufficient_data" in render(second) and "q_ref" in render(second)
    assert "điểm rủi ro: —" in render(second)


def test_select_ranked_supplier_uses_scenario_not_ranking_again():
    first, _ = run(
        {},
        decision(
            mode="new",
            tasks=[Intent.RANK],
            product="DEMO-P07",
            quantity="300",
            unit="ram",
            relative_days=2,
        ),
    )
    second, backend = run(first["state"], decision(selection="cái thứ 1"))
    assert second["status"] == "completed"
    assert backend.calls[-1].task == Intent.SCORE


def test_error_is_not_reported_as_no_product_or_ranking():
    output, backend = run(
        {}, decision(mode="new", tasks=[Intent.RANK], product="DEMO-P07"), Backend(fail=True)
    )
    assert output["status"] == "error" and len(backend.calls) == 1
    assert "BACKEND_UNAVAILABLE" in render(output)
    assert "Kết quả xếp hạng" not in render(output)


def test_quantity_update_after_ranking_reexecutes_ml_without_asking_to_choose_supplier():
    first, _ = run(
        {},
        decision(
            mode="new",
            tasks=[Intent.RANK],
            product="DEMO-P07",
            quantity="300",
            unit="ram",
            relative_days=2,
        ),
    )
    second, backend = run(first["state"], decision(quantity="500"))
    assert second["status"] == "completed"
    assert [r.task for r in backend.calls] == [Intent.RANK]
    assert backend.calls[0].arguments["quantity"] == "500"


def test_resolve_without_a_selector_never_claims_verified():
    output, backend = run({}, decision(mode="new", tasks=[Intent.RESOLVE]))
    assert output["status"] == "needs_input" and backend.calls == []


def test_multiline_pr_waits_for_line_and_uses_selected_line_only():
    class PRBackend(Backend):
        async def __call__(self, request):
            if request.task == Intent.REQUEST:
                self.calls.append(request)
                return tool_result(
                    "purchase_get_request",
                    request.arguments,
                    {
                        "request_id": str(uuid4()),
                        "request_code": "PR-001",
                        "items": [
                            {
                                "product_id": PRODUCT,
                                "quantity": "10",
                                "unit": "ram",
                                "requested_delivery_date": "2026-10-20",
                            },
                            {
                                "product_id": OTHER,
                                "quantity": "22",
                                "unit": "cuộn",
                                "requested_delivery_date": "2026-10-21",
                            },
                        ],
                    },
                )
            if request.task == Intent.RESOLVE and request.arguments.get("product") == OTHER:
                self.calls.append(request)
                return tool_result(
                    "catalog_resolve_entities",
                    request.arguments,
                    resolved(request.arguments, TAPES[0]),
                )
            return await super().__call__(request)

    backend = PRBackend()
    first, _ = run({}, decision(mode="new", tasks=[Intent.RANK], request="PR-001"), backend)
    assert first["status"] == "needs_choice" and len(backend.calls) == 1
    second, _ = run(first["state"], decision(selection="dòng thứ 2"), backend)
    assert second["status"] == "completed"
    assert backend.calls[-1].arguments["product_id"] == OTHER
    assert backend.calls[-1].arguments["quantity"] == "22"


def test_state_isolation_cas_lease_expiry_and_ttl(tmp_path):
    store = StateStore(tmp_path / "state.sqlite", ttl=2, lease_seconds=2)
    a = store.scope("u1", "flow", "session")
    b = store.scope("u2", "flow", "session")
    first = store.acquire(a)
    with pytest.raises(ValueError, match="SESSION_BUSY"):
        store.acquire(a)
    store.save(first, {"private": "u1"})
    assert store.acquire(b)["state"] == {}
    current = store.acquire(a)
    assert current["state"] == {"private": "u1"}
    with pytest.raises(ValueError, match="STATE_VERSION_CONFLICT"):
        store.save(first, {})
    store.release(current)
    with store.connection() as db:
        db.execute(
            "UPDATE conversations SET expires=?, lease_until=0 WHERE scope=?", (time.time() - 1, a)
        )
    assert store.acquire(a)["state"] == {}


def test_gate_fast_path_never_builds_or_invokes_model():
    context = {"ticket": {"state": new_state()}, "question": "Bạn là ai?", "business_date": DAY}
    gate = RequestNormalizer(context=Data(data=context))
    with patch.object(RequestNormalizer, "model_decision", new=AsyncMock()) as mocked:
        output = asyncio.run(gate.normalize()).data
    mocked.assert_not_called()
    assert output["model_calls"] == 0 and output["direct_reply"]


def test_gate_structured_call_is_one_request_and_records_tokens_without_retry():
    context = {
        "ticket": {"state": new_state()},
        "question": "Chào bạn, tìm NCC giấy",
        "business_date": DAY,
    }
    parsed = decision(mode="new", tasks=[Intent.RANK], product="giấy")
    response = {
        "parsed": parsed,
        "raw": AIMessage(
            content="",
            usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        ),
        "parsing_error": None,
    }
    gate = RequestNormalizer(context=Data(data=context), api_key="placeholder-not-a-key")
    with patch("flowbridge_agent.ver3_nodes.ChatOpenAI") as model:
        chain = model.return_value.with_structured_output.return_value
        chain.ainvoke = AsyncMock(return_value=response)
        output = asyncio.run(gate.normalize()).data
    assert output["model_calls"] == 1
    chain.ainvoke.assert_awaited_once()
    assert model.call_args.kwargs["max_retries"] == 0
    assert output["usage"][0]["tokens"]["total_tokens"] == 140


def test_bridge_only_accepts_runtime_scoped_request_and_uses_existing_adapter():
    client = DataBackend(BackendConfig())
    client.execute = AsyncMock(return_value=resolved({"product": "DEMO-P07"}))
    tool = DataActionTool(
        name="catalog_resolve_entities",
        description="test",
        action="catalog_resolve_entities",
        client=client,
    )
    request = ServiceRequest(
        run_id="run",
        task_id="task",
        service="database",
        task=Intent.RESOLVE,
        arguments={"product": "DEMO-P07"},
    )
    bridge = ServiceBridge(service="database", tools=[tool], input_value=request.model_dump_json())
    rejected = json.loads(asyncio.run(bridge.execute_request()).text)
    assert rejected["error"]["code"] == "UNTRUSTED_SERVICE_REQUEST"
    client.execute.assert_not_awaited()
    scope = Boundary(request)
    token = BOUNDARY.set(scope)
    try:
        accepted = json.loads(asyncio.run(bridge.execute_request()).text)
    finally:
        BOUNDARY.reset(token)
    assert accepted["status"] == "success"
    assert scope.receipts["run"] == accepted
    client.execute.assert_awaited_once()


def test_cloned_graph_preserves_api_nodes_exactly_and_replaces_llm_specialists():
    for service in ("database", "prediction", "knowledge", "central"):
        source = json.loads((ROOT / "flows/ver2" / (service + ".json")).read_text(encoding="utf-8"))
        result = asyncio.run(upgrade(cloned(source), service))
        original_tools = [
            n for n in source["data"]["nodes"] if "api_origin" in n["data"]["node"]["template"]
        ]
        copied_tools = [
            n for n in result["data"]["nodes"] if "api_origin" in n["data"]["node"]["template"]
        ]
        assert copied_tools == original_tools
        assert all(n["data"]["type"] != "Agent" for n in result["data"]["nodes"])
        mapping = {n["id"]: n for n in result["data"]["nodes"]}
        for edge in result["data"]["edges"]:
            field = edge["data"]["targetHandle"]["fieldName"]
            target = mapping[edge["target"]]["data"]["node"]["template"][field]
            output = next(
                o
                for o in mapping[edge["source"]]["data"]["node"]["outputs"]
                if o["name"] == edge["data"]["sourceHandle"]["name"]
            )
            assert set(output["types"]) & set(target.get("input_types", []))


def test_id_remapping_preserves_everything_else():
    original = {"name": "FlowBridge - ver2", "data": {"target": "old-id", "preset": "6000"}}
    assert replace_values(original, {"old-id": "new-id"}) == {
        "name": "FlowBridge - ver2",
        "data": {"target": "new-id", "preset": "6000"},
    }


def test_renderer_keeps_trace_in_native_session_metadata_and_shows_missing_ml_fields():
    receipt = tool_result(
        "prediction_rank_suppliers",
        {},
        {
            "status": "insufficient_data",
            "candidates": [],
            "unscored": [
                {
                    "supplier_name": "Phúc Lâm",
                    "unit_price": "50000",
                    "currency": "VND",
                    "result": {"status": "insufficient_data", "missing_fields": ["q_ref"]},
                }
            ],
        },
    )
    output = Workflow(Backend()).output(new_state(), "completed", result=receipt)
    output["trace"].update(model_calls=0, usage=[])
    message = AnswerRenderer(result=Data(data=output)).answer().model_dump()
    assert message["session_metadata"]["flowbridge_ver3"]["model_calls"] == 0
    assert "q_ref" in message["text"] and "Phúc Lâm" in message["text"]
    assert "Kết quả xếp hạng" not in message["text"]
    assert message["properties"]["usage"]["total_tokens"] == 0


def test_bridge_preserves_soft_defaults_and_explicitly_clears_filters():
    client = DataBackend(BackendConfig())
    client.execute = AsyncMock(return_value={"status": "success", "candidates": []})
    tool = configured_tool(
        "prediction_rank_suppliers",
        client,
        name="prediction_rank_suppliers",
        description="test",
        values={"top_k": 5, "currency": "VND", "max_unit_price": "60000"},
    )
    request = ServiceRequest(
        run_id="run",
        task_id="task",
        service="prediction",
        task=Intent.RANK,
        arguments={
            "product_id": PRODUCT,
            "quantity": "300",
            "requested_delivery_date": "2026-10-20",
            "clear_defaults": ["max_unit_price", "currency"],
        },
    )
    bridge = ServiceBridge(
        service="prediction", tools=[tool], input_value=request.model_dump_json()
    )
    scope = Boundary(request)
    token = BOUNDARY.set(scope)
    try:
        result = json.loads(asyncio.run(bridge.execute_request()).text)
    finally:
        BOUNDARY.reset(token)
    assert result["status"] == "success"
    assert result["input"]["top_k"] == 5
    assert result["input"]["currency"] is None and result["input"]["max_unit_price"] is None


def test_bridge_returns_correlated_error_for_missing_tool():
    request = ServiceRequest(
        run_id="run",
        task_id="task",
        service="knowledge",
        task=Intent.RETRIEVE,
        arguments={"query": "tài liệu"},
    )
    scope = Boundary(request)
    token = BOUNDARY.set(scope)
    try:
        result = json.loads(
            asyncio.run(
                ServiceBridge(
                    service="knowledge", tools=[], input_value=request.model_dump_json()
                ).execute_request()
            ).text
        )
    finally:
        BOUNDARY.reset(token)
    assert result["error"]["code"] == "SERVICE_TOOL_UNAVAILABLE"
    assert scope.receipts["run"] == result


def test_top_k_limits_follow_the_destination_api():
    assert normalize_slots(decision(top_k=20).slots, DAY)["top_k"] == 20
    output, backend = run(
        {},
        decision(
            mode="new",
            tasks=[Intent.RANK],
            product="DEMO-P07",
            quantity="300",
            unit="ram",
            relative_days=2,
            top_k=11,
        ),
    )
    assert output["status"] == "needs_input"
    assert [r.task for r in backend.calls] == [Intent.RESOLVE]
