# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from flowbridge_agent.ver2_agent import OpenAICompatibleAgent  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402


class Ver2ModelCompatibilityTests(unittest.TestCase):
    def agent(self, **kwargs):
        return OpenAICompatibleAgent(
            model=[{"name": "gpt-5.6-luna", "provider": "OpenAI"}], **kwargs
        )

    def test_default_factory_options_and_sdk_tool_payload(self):
        agent = self.agent()
        sentinel = object()
        factory = "lfx.components.models_and_agents.agent.get_llm"
        with patch(factory, return_value=sentinel) as mocked:
            self.assertIs(agent._get_llm(), sentinel)
        options = mocked.call_args.kwargs["overrides"]
        self.assertEqual(options["reasoning_effort"], "none")
        self.assertFalse(options["use_responses_api"])
        self.assertEqual(options["max_retries"], 0)
        # Construct/serialize only: never invoke the SDK or an Agent.
        llm = ChatOpenAI(model="gpt-5.6-luna", api_key="offline-placeholder", **options)
        tool = {
            "type": "function",
            "function": {
                "name": "hoi_du_lieu",
                "description": "Read data",
                "parameters": {
                    "type": "object",
                    "properties": {"input_value": {"type": "string"}},
                    "required": ["input_value"],
                },
            },
        }
        payload = llm._get_request_payload([HumanMessage(content="offline")], tools=[tool])
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertEqual(payload["tools"][0]["function"]["name"], "hoi_du_lieu")
        self.assertFalse(llm._use_responses_api(payload))

    def test_reasoning_on_chat_fails_before_model_construction(self):
        agent = self.agent(openai_reasoning_effort="low")
        with patch("lfx.components.models_and_agents.agent.get_llm") as mocked:
            with self.assertRaisesRegex(ValueError, "Responses"):
                agent._get_llm()
            mocked.assert_not_called()

    def test_responses_allows_reasoning(self):
        agent = self.agent(openai_api_mode="Responses", openai_reasoning_effort="low")
        with patch("lfx.components.models_and_agents.agent.get_llm") as mocked:
            agent._get_llm()
        options = mocked.call_args.kwargs["overrides"]
        self.assertTrue(options["use_responses_api"])
        self.assertEqual(options["reasoning_effort"], "low")


if __name__ == "__main__":
    unittest.main()
