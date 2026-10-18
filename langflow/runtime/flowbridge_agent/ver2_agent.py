# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from copy import deepcopy
from typing import Any

from lfx.components.models_and_agents.agent import AgentComponent
from lfx.io import DropdownInput, IntInput


class OpenAICompatibleAgent(AgentComponent):
    async def get_memory_data(self) -> list:
        from flowbridge_agent.caller import CALLER

        if CALLER.get() is not None:
            return []
        return await super().get_memory_data()

    async def send_message(self, message: Any, *args: Any, **kwargs: Any) -> Any:
        from flowbridge_agent.caller import CALLER

        if CALLER.get() is not None:
            return message
        return await super().send_message(message, *args, **kwargs)

    # Preserve Langflow orchestration/memory/structured output; configure its model factory.
    inputs = [
        *deepcopy(AgentComponent.inputs),
        DropdownInput(
            name="openai_api_mode",
            display_name="Endpoint OpenAI",
            value="Chat Completions",
            options=["Chat Completions", "Responses"],
            info="Luna + tool trên Chat Completions cần none. Responses cho phép reasoning.",
        ),
        DropdownInput(
            name="openai_reasoning_effort",
            display_name="Mức suy luận OpenAI",
            value="none",
            options=["none", "low", "medium", "high", "xhigh", "max"],
            info="none dùng được với tool trên Chat Completions và giảm reasoning token. "
            "Muốn dùng low trở lên với Luna, chọn endpoint Responses.",
        ),
        IntInput(
            name="openai_sdk_retries",
            display_name="Số lần SDK thử lại",
            value=0,
            advanced=True,
        ),
        IntInput(
            name="openai_timeout",
            display_name="Timeout OpenAI (giây)",
            value=45,
            advanced=True,
        ),
    ]

    def _get_llm(self) -> Any:
        selected = self._resolve_selected_model()
        if (
            not isinstance(selected, list)
            or not selected
            or selected[0].get("provider") != "OpenAI"
        ):
            return super()._get_llm()
        name = selected[0].get("name", "")
        mode = self.openai_api_mode
        effort = self.openai_reasoning_effort
        if mode not in {"Chat Completions", "Responses"}:
            raise ValueError("Chọn Chat Completions hoặc Responses")
        if name.startswith("gpt-5.6-luna") and mode == "Chat Completions" and effort != "none":
            raise ValueError(
                "GPT-5.6 Luna + tool: đặt Mức suy luận=none trên Chat Completions, "
                "hoặc chọn Endpoint OpenAI=Responses để dùng reasoning. Chưa gửi request."
            )
        retries, timeout = int(self.openai_sdk_retries), int(self.openai_timeout)
        if not 0 <= retries <= 3 or not 1 <= timeout <= 600:
            raise ValueError("SDK retries: 0–3; timeout: 1–600 giây")
        overrides = {
            **(getattr(self, "_model_overrides", None) or {}),
            "use_responses_api": mode == "Responses",
            "max_retries": retries,
            "timeout": timeout,
        }
        if name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
            overrides["reasoning_effort"] = effort
        self._model_overrides = overrides
        return super()._get_llm()
