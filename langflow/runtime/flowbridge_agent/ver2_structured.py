# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from typing import Any

from langchain_openai import ChatOpenAI
from lfx.schema.data import Data

from flowbridge_agent.ver2_agent import OpenAICompatibleAgent


def has_open_objects(schema: Any) -> bool:
    if hasattr(schema, "model_json_schema"):
        schema = schema.model_json_schema()
    if isinstance(schema, dict):
        if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
            return True
        return any(has_open_objects(value) for value in schema.values())
    if isinstance(schema, list):
        return any(has_open_objects(value) for value in schema)
    return False


class NativeOutputCompatibility:
    # Only passed to the native output formatter, never to the agent/tool execution loop.
    def __init__(self, model: ChatOpenAI) -> None:
        self.model = model

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        if has_open_objects(schema):
            # Strict JSON Schema cannot represent arbitrary ToolResult payloads.
            # LangChain still parses/validates the returned Pydantic model locally.
            kwargs.update(method="function_calling", strict=False)
        return self.model.with_structured_output(schema, **kwargs)


class StructuredServiceAgent(OpenAICompatibleAgent):
    # Retain upstream json_response orchestration, memory, tools and fallback parsing.
    async def json_response(self) -> Data:
        self._flowbridge_structured_response = True
        try:
            return await super().json_response()
        finally:
            self._flowbridge_structured_response = False

    async def get_agent_requirements(self) -> tuple[Any, Any, Any]:
        model, history, tools = await super().get_agent_requirements()
        if (
            getattr(self, "_flowbridge_structured_response", False)
            and not tools
            and getattr(self, "output_schema", None)
            and isinstance(model, ChatOpenAI)
        ):
            model = NativeOutputCompatibility(model)
        return model, history, tools
