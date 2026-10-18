# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from flowbridge_agent.ver2_prompts import RESPONSE_EXAMPLE, output_table  # noqa: E402
from flowbridge_agent.ver2_structured import (  # noqa: E402
    NativeOutputCompatibility,
    StructuredServiceAgent,
    has_open_objects,
)
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from lfx.components.models_and_agents.agent import AgentComponent  # noqa: E402
from lfx.components.models_and_agents.structured_output.structured_output_orchestrator import (  # noqa: E402
    _wrap_in_list_container,
    orchestrate_structured_output,
)
from lfx.helpers.base_model import build_model_from_schema  # noqa: E402


def model():
    return ChatOpenAI(
        model="gpt-5.6-luna",
        api_key="offline-placeholder",
        reasoning_effort="none",
        use_responses_api=False,
        max_retries=0,
    )


class StructuredOutputTests(unittest.IsolatedAsyncioTestCase):
    def test_dynamic_schema_uses_nonstrict_function_format_without_dropping_fields(self):
        llm = model()
        schema = _wrap_in_list_container(build_model_from_schema(output_table()), "objects")
        self.assertTrue(has_open_objects(schema))
        runnable = NativeOutputCompatibility(llm).with_structured_output(schema)
        options = runnable.steps[0].kwargs
        self.assertFalse(options["tools"][0]["function"]["strict"])
        self.assertNotIn("response_format", options)
        definition = options["tools"][0]["function"]["parameters"]
        encoded = json.dumps(definition)
        self.assertIn('"resolved"', encoded)
        self.assertIn('"additionalProperties": true', encoded)
        payload = llm._get_request_payload([HumanMessage(content="offline")], **options)
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertFalse(payload["tools"][0]["function"]["strict"])

    async def test_native_output_roundtrip_retains_nested_json_and_uses_one_mock_call(self):
        record = deepcopy(RESPONSE_EXAMPLE)
        record.update(service="knowledge", status="success", missing=[], questions=[])
        record["resolved"] = {"supplier_id": "6b803be4-eaa1-5bac-bb84-1b7334475e7c"}
        record["results"] = [
            {
                "tool": "knowledge_search_supplier",
                "status": "success",
                "input": {"query": "chứng nhận", "top_k": 3},
                "data": {
                    "chunks": [
                        {
                            "text": 'Chứng nhận "A"\nDòng 2',
                            "reference": {
                                "document_id": "doc-1",
                                "version": 2,
                                "checksum": "abc",
                            },
                        }
                    ]
                },
                "error": None,
            }
        ]
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "OutputModel",
                    "id": "offline-call",
                    "args": {"objects": [record]},
                }
            ],
        )
        mocked = AsyncMock(return_value=message)
        fallback = AsyncMock(side_effect=AssertionError("Must not retry through prompt fallback"))
        with patch.object(ChatOpenAI, "ainvoke", mocked):
            result = await orchestrate_structured_output(
                llm=NativeOutputCompatibility(model()),
                output_schema=output_table(),
                system_prompt="JSON output",
                format_instructions="",
                input_value="offline",
                run_prompt_fallback=fallback,
            )
        self.assertEqual(result.data, record)
        mocked.assert_awaited_once()
        fallback.assert_not_awaited()

    async def test_invalid_native_output_is_rejected_by_local_pydantic_validation(self):
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "OutputModel",
                    "id": "offline-call",
                    "args": {"objects": [{"service": 42}]},
                }
            ],
        )
        with patch.object(ChatOpenAI, "ainvoke", AsyncMock(return_value=message)):
            with self.assertRaises(ValueError):
                await orchestrate_structured_output(
                    llm=NativeOutputCompatibility(model()),
                    output_schema=output_table(),
                    system_prompt="JSON output",
                    format_instructions="",
                    input_value="offline",
                    run_prompt_fallback=AsyncMock(),
                )

    async def test_service_agent_runs_upstream_native_path_and_resets_compatibility_scope(self):
        record = {**deepcopy(RESPONSE_EXAMPLE), "service": "knowledge", "status": "unavailable"}
        agent = StructuredServiceAgent(
            output_schema=output_table(), system_prompt="Return JSON", input_value="offline"
        )
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "OutputModel",
                    "id": "offline-call",
                    "args": {"objects": [record]},
                }
            ],
        )
        invoked = AsyncMock(return_value=message)
        requirements = AsyncMock(return_value=(model(), [], []))
        with (
            patch.object(AgentComponent, "get_agent_requirements", requirements),
            patch.object(ChatOpenAI, "ainvoke", invoked),
        ):
            result = await agent.json_response()
        self.assertEqual(result.data, record)
        self.assertFalse(agent._flowbridge_structured_response)
        invoked.assert_awaited_once()

    async def test_agent_wraps_only_native_structured_path_without_tools(self):
        llm = model()
        agent = StructuredServiceAgent(output_schema=output_table())
        with patch.object(
            AgentComponent, "get_agent_requirements", AsyncMock(return_value=(llm, [], []))
        ):
            normal, _, _ = await agent.get_agent_requirements()
            self.assertIs(normal, llm)
            agent._flowbridge_structured_response = True
            structured, _, _ = await agent.get_agent_requirements()
            self.assertIsInstance(structured, NativeOutputCompatibility)
        tool = object()
        with patch.object(
            AgentComponent, "get_agent_requirements", AsyncMock(return_value=(llm, [], [tool]))
        ):
            original, _, tools = await agent.get_agent_requirements()
            self.assertIs(original, llm)
            self.assertEqual(tools, [tool])

    def test_closed_schema_retains_provider_native_default(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }
        llm = model()
        with patch.object(ChatOpenAI, "with_structured_output", return_value="sentinel") as mocked:
            self.assertEqual(
                NativeOutputCompatibility(llm).with_structured_output(schema), "sentinel"
            )
        mocked.assert_called_once_with(schema)


if __name__ == "__main__":
    unittest.main()
