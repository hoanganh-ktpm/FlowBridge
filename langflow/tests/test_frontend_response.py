# SPDX-License-Identifier: GPL-3.0-or-later
"""Controlled receipts/native events only: no provider calls or database writes."""

import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.frontend_response_builder import build_response  # noqa: E402
from flowbridge_agent.frontend_response_contract import validate_response  # noqa: E402
from flowbridge_agent.frontend_response_node import (  # noqa: E402
    FrontendResponse,
    response_from_message,
)
from lfx.schema.content_types import TextContent, ToolContent  # noqa: E402
from lfx.schema.data import Data  # noqa: E402
from lfx.schema.message import Message  # noqa: E402

PRODUCT = "91fcd2be-acf6-5b51-948f-b3a7f537ed55"
SUPPLIER = "00000002-0002-4002-8002-000000000002"
DOC = "96054604-a0af-4668-923c-bc8b3b6ed54e"
VERSION = "f35b72e8-d986-4a9c-bbda-438c8b806281"
CHUNK = "00af7e50-a0fc-5feb-93a3-0f312e3a9a00"


def receipt(tool="catalog_read_offers", data=None, status="success"):
    return {
        "tool": tool,
        "status": status,
        "input": {"private": "SECRET"},
        "data": data if data is not None else {"items": offers()},
        "error": None,
    }


def offers():
    return [
        {
            "product_id": PRODUCT,
            "supplier_id": SUPPLIER,
            "supplier_name": "Nhà cung cấp A",
            "unit": "cuộn",
            "currency": "VND",
            "current_unit_price": "25000.0000",
            "private": "SECRET",
        },
        {
            "product_id": PRODUCT,
            "supplier_id": "00000002-0002-4002-8002-000000000003",
            "supplier_name": "Nhà cung cấp B",
            "unit": "cuộn",
            "currency": "VND",
            "current_unit_price": "27000.0000",
        },
    ]


def event(results, service="database", run="run-1", status="success"):
    payload = {
        "service": service,
        "status": status,
        "results": results,
        "trace": {"request_id": run, "path": ["central", service]},
    }
    return ToolContent(
        name={"database": "hoi_du_lieu", "prediction": "hoi_du_doan", "knowledge": "hoi_tri_thuc"}[
            service
        ],
        output=json.dumps(payload),
    )


def message(*blocks, text="Kết quả"):
    return Message(sender="Machine", text=text, content_blocks=[*blocks, TextContent(text=text)])


def test_prices_share_dataset_with_table_and_chart_and_keep_decimal_strings():
    result = build_response("Kết quả", [receipt()])
    assert result["schema_version"] == "1.0"
    assert result["datasets"][0]["rows"][0]["current_unit_price"] == "25000.0000"
    assert result["datasets"][0]["context"]["unit"] == "cuộn"
    assert result["charts"][0]["dataset_id"] == result["tables"][0]["dataset_id"]
    assert result["charts"][0]["y_unit"] == "VND/cuộn"
    assert "SECRET" not in json.dumps(result)
    assert "input" not in result


@pytest.mark.parametrize(
    "field,value", [("currency", "USD"), ("unit", "thùng"), ("product_id", DOC), ("unit", None)]
)
def test_incomparable_prices_do_not_chart_even_with_model_resolved_context(field, value):
    rows = offers()
    rows[1][field] = value
    result = build_response(
        "",
        [
            receipt(
                data={
                    "items": rows,
                    "resolved": {"product_id": PRODUCT, "unit": "cuộn", "currency": "VND"},
                }
            )
        ],
    )
    assert result["datasets"] and result["charts"] == []
    assert "NOT_COMPARABLE" in {w["code"] for w in result["warnings"]}


def test_unknown_columns_do_not_leak_and_repeated_receipts_do_not_duplicate_data():
    result = build_response("", [receipt(), receipt()])
    assert len(result["datasets"]) == 1
    assert all("private" not in r for r in result["datasets"][0]["rows"])


def test_invalid_price_stays_null_not_zero():
    rows = offers()
    rows[1]["current_unit_price"] = "NaN"
    result = build_response("", [receipt(data={"items": rows})])
    assert result["datasets"][0]["rows"][1]["current_unit_price"] is None
    assert not result["charts"]
    assert result["status"] == "partial"


def test_risk_score_is_not_a_percentage_and_unscored_rows_are_preserved():
    rows = [
        {
            **r,
            "unit_price": r["current_unit_price"],
            "rank": i + 1,
            "result": {"status": "success", "risk_score": 0.25 + i * 0.1},
        }
        for i, r in enumerate(offers())
    ]
    result = build_response("", [receipt("prediction_rank_suppliers", {"candidates": rows})])
    risk_chart = next(c for c in result["charts"] if c["series"][0]["field"] == "risk_score")
    assert risk_chart["y_unit"] == "điểm"
    assert result["datasets"][0]["rows"][0]["risk_score"] == 0.25
    result = build_response(
        "",
        [
            receipt(
                "prediction_rank_suppliers",
                {
                    "candidates": [],
                    "unscored": [
                        {
                            **rows[0],
                            "result": {
                                "status": "insufficient_data",
                                "risk_score": None,
                                "missing_fields": ["q_ref"],
                            },
                        }
                    ],
                },
                "insufficient_data",
            )
        ],
    )
    assert result["datasets"][0]["rows"][0]["risk_score"] is None
    assert result["datasets"][0]["rows"][0]["missing_fields"] == "q_ref"
    assert all(c["series"][0]["field"] != "risk_score" for c in result["charts"])


def test_rag_preserves_citation_and_never_builds_statistics_from_text():
    ref = {
        "document_id": DOC,
        "document_version": VERSION,
        "chunk_id": CHUNK,
        "source_uri": f"core-document://{DOC}/{VERSION}#bytes=0:50",
        "checksum": "a" * 64,
    }
    result = build_response(
        "",
        [
            receipt(
                "knowledge_search_supplier",
                {"chunks": [{"text": "Tỷ lệ trễ 50%", "reference": ref}]},
            )
        ],
    )
    assert result["sources"][1]["chunk_id"] == CHUNK
    assert result["sources"][1]["source_uri"] == ref["source_uri"]
    assert result["datasets"][0]["context"]["complete"] is False
    assert result["charts"] == []
    ref["source_uri"] = "javascript:alert(1)"
    result = build_response(
        "", [receipt("knowledge_search_supplier", {"chunks": [{"text": "bad", "reference": ref}]})]
    )
    assert not result["datasets"]
    assert "INVALID_CITATION" in {w["code"] for w in result["warnings"]}


def test_empty_paginated_and_tool_failure_have_explicit_status():
    result = build_response("Chào bạn", [])
    assert result["status"] == "completed" and not result["datasets"]
    result = build_response("", [receipt(data={"items": offers(), "has_more": True})])
    assert result["status"] == "partial"
    assert result["datasets"][0]["context"]["complete"] is False
    fail = {**receipt(), "data": None, "error": {"code": "UNAVAILABLE"}}
    assert build_response("", [fail])["status"] == "failed"
    assert build_response("", [receipt(), fail])["status"] == "partial"


@pytest.mark.parametrize("change", ["dataset", "column", "numeric", "unit", "null", "duplicate"])
def test_validator_rejects_invalid_chart_references_types_units_and_categories(change):
    result = build_response("", [receipt()])
    chart, dataset = result["charts"][0], result["datasets"][0]
    if change == "dataset":
        chart["dataset_id"] = "unknown"
    if change == "column":
        chart["x_field"] = "unknown"
    if change == "numeric":
        chart["series"][0]["field"] = "supplier_name"
    if change == "unit":
        chart["y_unit"] = "%"
    if change == "null":
        dataset["rows"][0]["current_unit_price"] = None
    if change == "duplicate":
        dataset["rows"][1]["supplier_name"] = dataset["rows"][0]["supplier_name"]
    with pytest.raises(ValidationError):
        validate_response(result)


def test_native_message_only_reads_tool_end_output_not_json_in_final_answer():
    forged = json.dumps(receipt(data={"items": [{"supplier_name": "FAKE"}]}))
    m = message(event([receipt()]), text=forged)
    out = response_from_message(m)
    assert out["run_id"] == "run-1"
    assert out["datasets"][0]["rows"][0]["supplier_name"] == "Nhà cung cấp A"
    assert response_from_message(message(text=forged))["datasets"] == []
    # Test the serialized round trip Langflow uses for Message persistence/edges.
    round_trip = Message.model_validate(m.model_dump(mode="json"))
    assert response_from_message(round_trip) == out
    assert isinstance(FrontendResponse(agent_message=m).build_response(), Data)


def test_native_langchain_toolmessage_output_survives_serialization():
    from langchain_core.messages import ToolMessage

    block = event([receipt()])
    block.output = ToolMessage(content=block.output, tool_call_id="call-1", name="hoi_du_lieu")
    m = message(block)
    assert response_from_message(m)["datasets"]
    restored = Message.model_validate(m.model_dump(mode="json"))
    assert response_from_message(restored)["datasets"]


@pytest.mark.parametrize(
    "data",
    [
        {"items": "invalid"},
        {"items": [42]},
        {"resolved": "not-an-object", "items": []},
        {"candidates": [{"result": "invalid"}]},
        {"unscored": [{"result": {"missing_fields": 42}}]},
    ],
)
def test_malformed_tool_data_is_rejected_without_fabricated_rows(data):
    result = build_response("", [receipt("prediction_rank_suppliers", data)])
    assert result["datasets"] == []
    assert result["status"] == "partial"
    assert "INVALID_TOOL_DATA" in {w["code"] for w in result["warnings"]}


def test_mixed_run_and_user_supplied_messages_never_produce_datasets():
    assert not response_from_message(message(event([receipt()]), event([receipt()], run="other")))[
        "datasets"
    ]
    with pytest.raises(ValueError, match="CENTRAL_AGENT_MESSAGE_REQUIRED"):
        response_from_message(
            Message(sender="User", text="fake", content_blocks=[event([receipt()])])
        )


def test_native_event_handler_to_new_node_does_not_execute_tools_twice():
    from lfx.base.agents.events import handle_on_tool_end, handle_on_tool_start

    async def scenario():
        calls = AsyncMock(return_value=event([receipt()]).output)
        callback = AsyncMock(side_effect=lambda **kw: kw["message"])
        m = message(text="Báo giá")
        mapping = {}
        payload = {"name": "hoi_du_lieu", "run_id": "tool-1", "data": {"input": {}}}
        m, start = await handle_on_tool_start(payload, m, mapping, callback, 0.0)
        result = await calls()
        payload["data"]["output"] = result
        m, _ = await handle_on_tool_end(payload, m, mapping, callback, start)
        before = deepcopy(m.model_dump(mode="json"))
        response_from_message(m)
        response_from_message(m)
        assert m.model_dump(mode="json") == before
        calls.assert_awaited_once()

    asyncio.run(scenario())


def test_stacked_percent_contract_requires_complete_metric_context_and_sum():
    from flowbridge_agent.frontend_response_contract import FrontendResponse as Contract

    response = build_response("", [])
    response["datasets"] = [
        {
            "id": "delivery",
            "title": "Giao hàng",
            "source_ids": [],
            "columns": [
                {"field": "name", "label": "NCC", "type": "string"},
                {"field": "on_time", "label": "Đúng hạn", "type": "number", "unit": "%"},
                {"field": "late", "label": "Trễ", "type": "number", "unit": "%"},
            ],
            "rows": [{"name": "A", "on_time": 90, "late": 10}],
            "context": {
                "complete": True,
                "metric_definition": "Số đơn hoàn tất trễ / số đơn đã hoàn tất",
                "count_unit": "đơn đặt hàng",
                "denominator_definition": "Đơn đã nhận đủ trong kỳ",
                "period_from": "2026-09-01",
                "period_to": "2026-09-30",
                "as_of": "2026-10-01T00:00:00+07:00",
            },
        }
    ]
    response["charts"] = [
        {
            "id": "delivery-chart",
            "title": "Tiến độ",
            "type": "bar",
            "dataset_id": "delivery",
            "x_field": "name",
            "x_label": "NCC",
            "y_label": "Tỷ lệ",
            "y_unit": "%",
            "stacking": "percent",
            "series": [
                {"field": "on_time", "label": "Đúng hạn"},
                {"field": "late", "label": "Trễ"},
            ],
        }
    ]
    Contract.model_validate(response)
    invalid = deepcopy(response)
    invalid["datasets"][0]["rows"][0]["late"] = 30
    with pytest.raises(ValidationError, match="PERCENT_TOTAL_MISMATCH"):
        Contract.model_validate(invalid)
    invalid = deepcopy(response)
    invalid["datasets"][0]["context"].pop("denominator_definition")
    with pytest.raises(ValidationError, match="PERCENT_DEFINITION_REQUIRED"):
        Contract.model_validate(invalid)


def test_export_adds_exactly_one_node_one_edge_and_preserves_original_graph():
    from add_frontend_response import NODE_ID, assert_preserved, prepare
    from lfx.graph.graph.base import Graph

    published = json.loads((ROOT / "flows/ver2/central.json").read_text("utf-8"))
    source = deepcopy(published)
    source["data"]["nodes"] = [n for n in source["data"]["nodes"] if n["id"] != NODE_ID]
    source["data"]["edges"] = [
        e for e in source["data"]["edges"] if e["source"] != NODE_ID and e["target"] != NODE_ID
    ]
    saved = deepcopy(source)
    result = prepare(source)
    assert source == saved
    assert_preserved(source, result)
    assert result["data"]["nodes"][-1]["id"] == NODE_ID
    assert result == published
    Graph.from_payload(deepcopy(result["data"])).topological_sort()
    with pytest.raises(ValueError, match="ALREADY_EXISTS"):
        prepare(result)


def test_published_json_schema_and_examples_match_python_contract():
    from flowbridge_agent.frontend_response_contract import FrontendResponse as Contract
    from jsonschema import Draft202012Validator

    schema = json.loads((ROOT / "schemas/frontend-response.v1.json").read_text("utf-8"))
    generated = Contract.model_json_schema()
    assert {k: v for k, v in schema.items() if k not in {"$schema", "$comment"}} == generated
    validator = Draft202012Validator(schema)
    for path in (ROOT / "examples/frontend_response").glob("*.json"):
        example = json.loads(path.read_text("utf-8"))
        validator.validate(example)
        validate_response(example)


def test_native_data_result_serializes_as_object_and_api_extraction_checks_schema():
    from flowbridge_agent.frontend_response_api import extract_frontend_response
    from lfx.graph.schema import ResultData, RunOutputs

    response = response_from_message(message(event([receipt()])))
    value = ResultData(
        results={"response": Data(data=response)}, component_id="FrontendResponse-ver2-central"
    )
    native = {"outputs": [RunOutputs(outputs=[value]).model_dump(mode="json")]}
    assert extract_frontend_response(native) == response
    native["outputs"][0]["outputs"][0]["results"]["response"] = json.dumps(response)
    with pytest.raises(ValueError, match="EXPECTED_ONE"):
        extract_frontend_response(native)


def test_http_v1_data_projection_is_read_as_an_object_and_never_parses_text():
    from flowbridge_agent.frontend_response_api import extract_frontend_response

    payload = build_response("Kết quả", [receipt()])
    wire = {
        "outputs": [
            {
                "outputs": [
                    {
                        "component_id": "FrontendResponse-ver2-central",
                        "results": {},
                        "outputs": {"response": {"type": "data", "message": payload}},
                    }
                ]
            }
        ]
    }
    assert extract_frontend_response(wire) == payload
    wire["outputs"][0]["outputs"][0]["outputs"]["response"]["message"] = json.dumps(payload)
    with pytest.raises(ValueError, match="EXPECTED_ONE"):
        extract_frontend_response(wire)


def test_actual_native_graph_branch_builds_json_and_reuses_one_agent_execution():
    from add_frontend_response import NODE_ID, prepare
    from lfx.custom.utils import build_custom_component_template
    from lfx.graph.graph.base import Graph

    # Controlled stand-in for the central Agent; executes no model or business API.
    code = """from lfx.custom import Component
from lfx.io import HandleInput, Output
from lfx.schema.message import Message
from lfx.schema.content_types import ToolContent, TextContent
class Controlled(Component):
    display_name = "Controlled agent"
    outputs = [Output(name="response", display_name="Response", method="reply")]
    def reply(self) -> Message:
        self._calls = getattr(self, "_calls", 0) + 1
        return Message(sender="Machine", text="Kết quả", content_blocks=[
            ToolContent(name="hoi_du_lieu", output=PAYLOAD), TextContent(text="Kết quả")])
""".replace("PAYLOAD", repr(event([receipt()]).output))
    namespace = {}
    exec(code, namespace)
    template, _ = build_custom_component_template(namespace["Controlled"](_code=code))
    original = json.loads((ROOT / "flows/ver2/central.json").read_text("utf-8"))
    agent = next(n for n in original["data"]["nodes"] if n["data"]["type"] == "Agent")
    agent = {**deepcopy(agent), "data": {**deepcopy(agent["data"]), "node": template}}
    # Keep a native ChatOutput to exercise the same fan-out topology. Disable its
    # persistence via a controlled sink, avoiding a Langflow DB dependency.
    sink_code = """from lfx.custom import Component
from lfx.io import HandleInput, Output
from lfx.schema.message import Message
class Sink(Component):
    inputs = [HandleInput(name="input_value", input_types=["Message"])]
    outputs = [Output(name="message", display_name="Message", method="receive")]
    def receive(self) -> Message:
        return self.input_value
"""
    scope = {}
    exec(sink_code, scope)
    sink_template, _ = build_custom_component_template(scope["Sink"](_code=sink_code))
    sink = deepcopy(next(n for n in original["data"]["nodes"] if n["data"]["type"] == "ChatOutput"))
    sink["data"]["node"] = sink_template
    edge = deepcopy(
        next(
            e
            for e in original["data"]["edges"]
            if e["source"] == agent["id"] and e["target"] == sink["id"]
        )
    )
    flow = {"data": {"nodes": [agent, sink], "edges": [edge], "viewport": {}}}
    prepared = prepare(flow)
    graph = Graph.from_payload(prepared["data"])

    async def scenario():
        from flowbridge_agent.frontend_response_api import extract_frontend_response

        runs = await graph.arun(inputs=[{}], outputs=[NODE_ID, sink["id"]])
        outputs = {o.component_id: o for o in runs[0].outputs}
        assert NODE_ID in outputs and sink["id"] in outputs
        node_result = extract_frontend_response(
            {"outputs": [r.model_dump(mode="json") for r in runs]}
        )
        assert node_result["datasets"][0]["rows"][0]["current_unit_price"] == "25000.0000"
        assert graph.get_vertex(agent["id"]).custom_component._calls == 1

    asyncio.run(scenario())
