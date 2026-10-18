# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

import json
from copy import deepcopy

from lfx.components.models_and_agents.a2a_agent import A2AAgentComponent
from lfx.components.models_and_agents.agent_helpers.single_tool_call_middleware import (
    SingleToolCallMiddleware,
)
from lfx.io import DropdownInput
from lfx.schema.data import Data
from lfx.schema.message import Message

from flowbridge_agent.ver2_agent import OpenAICompatibleAgent
from flowbridge_agent.ver2_coordination import (
    CURRENT,
    EDGES,
    PENDING,
    TASKS,
    CoordinationMiddleware,
    Reply,
    Request,
    finish_reply,
    invocation,
    loads,
    service_error,
    stamp,
    stopped,
)
from flowbridge_agent.ver2_structured import StructuredServiceAgent


class BoundedAgentMixin:
    def _build_middleware(self, llm, *, allow_interrupts=True):
        middleware = [
            m
            for m in super()._build_middleware(llm, allow_interrupts=allow_interrupts)
            if type(m).__name__ != "ToolRetryMiddleware"
        ]
        middleware.append(CoordinationMiddleware())
        if not any(isinstance(m, SingleToolCallMiddleware) for m in middleware):
            middleware.append(SingleToolCallMiddleware())
        return middleware


class CoordinatedCentralAgent(BoundedAgentMixin, OpenAICompatibleAgent):
    async def message_response(self) -> Message:
        with invocation("central"):
            try:
                return await super().message_response()
            except ValueError as exc:
                if str(exc) != "MODEL_BUDGET_EXHAUSTED":
                    raise
                return Message(
                    text="Lượt xử lý đã đạt giới hạn. Chưa đủ kết quả để kết luận; "
                    "bạn có thể thu hẹp yêu cầu hoặc tiếp tục phần còn thiếu."
                )


class CoordinatedServiceAgent(BoundedAgentMixin, StructuredServiceAgent):
    inputs = [
        *deepcopy(StructuredServiceAgent.inputs),
        DropdownInput(
            name="coordination_role",
            display_name="Vai trò phối hợp",
            options=["database", "prediction", "knowledge"],
            value="database",
            info="Khớp vai trò của flow; giới hạn tool và đường gọi theo vai trò này.",
        ),
    ]

    async def json_response(self) -> Data:
        role = str(self.coordination_role)
        if role not in TASKS:
            raise ValueError("Invalid service role")
        with invocation(role) as scope:
            # Missing RAG credentials are a configuration issue, not a reason to spend model calls.
            if role == "knowledge" and not any(
                getattr(t, "action", getattr(t, "name", "")) == "knowledge_search_supplier"
                for t in (self.tools or [])
            ):
                return Data(data=stopped("knowledge", "KNOWLEDGE_TOOL_UNAVAILABLE"))
            try:
                output = await super().json_response()
                raw = output.data
            except ValueError as exc:
                if str(exc) != "MODEL_BUDGET_EXHAUSTED":
                    raise
                scope.blocked.append("MODEL_BUDGET_EXHAUSTED")
                raw = stopped(role, "MODEL_BUDGET_EXHAUSTED")
            return Data(data=finish_reply(raw, scope))


class BoundedA2AAgent(A2AAgentComponent):
    # All execution, project ACLs and sub-session isolation remain in native Internal A2A.
    inputs = [
        *deepcopy(A2AAgentComponent.inputs),
        DropdownInput(
            name="caller_service",
            display_name="Agent gọi",
            options=["central", "prediction", "knowledge"],
            value="central",
        ),
        DropdownInput(
            name="target_service",
            display_name="Dịch vụ đích",
            options=["database", "prediction", "knowledge"],
            value="database",
        ),
    ]

    async def to_toolkit(self):
        tools = await super().to_toolkit()
        # Native UI refresh may restore send_to_agent; retain an unambiguous service action.
        name = {
            "database": "hoi_du_lieu",
            "prediction": "hoi_du_doan",
            "knowledge": "hoi_tri_thuc",
        }[str(self.target_service)]
        for tool in tools:
            tool.name = name
        return tools

    async def send_to_agent(self) -> Message:
        scope, target = CURRENT.get(), str(self.target_service)

        def fail(code):
            if scope:
                scope.blocked.append(code)
            reply = stopped(target, code)
            if code in {
                "INVALID_HANDOFF_REQUEST",
                "INVALID_AGENT_ROUTE",
                "AGENT_ROUTE_BLOCKED",
                "INVALID_SERVICE_REPLY",
                "SERVICE_MISMATCH",
                "UNCORRELATED_SERVICE_REPLY",
            }:
                reply["status"] = "error"
            return Message(
                text=json.dumps(
                    stamp(Reply.model_validate(reply), scope) if scope else reply,
                    ensure_ascii=False,
                )
            )

        if self.mode != "Internal" or scope is None or scope.role != self.caller_service:
            return fail("INVALID_AGENT_ROUTE")
        if target not in EDGES[scope.role] or target in scope.path or len(scope.path) >= 3:
            return fail("AGENT_ROUTE_BLOCKED")
        try:
            request = Request.model_validate(loads(self.input_value))
            if request.task not in TASKS[target]:
                raise ValueError("Unsupported task")
            if scope.role == "prediction" and request.task not in {
                "resolve_entities",
                "request",
                "order",
            }:
                raise ValueError("Unsupported prediction dependency")
            if scope.role == "knowledge" and request.task != "resolve_entities":
                raise ValueError("Unsupported knowledge dependency")
            # Evidence may only forward real results observed during this root execution.
            observed = scope.run.evidence
            if any(
                json.dumps(r.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
                not in observed
                for r in request.evidence
            ):
                raise ValueError("Unobserved evidence")
        except (ValueError, TypeError):
            return fail("INVALID_HANDOFF_REQUEST")
        payload = request.model_dump(mode="json")
        key = json.dumps([target, payload], sort_keys=True, ensure_ascii=False)
        if key in scope.run.replies:
            return Message(text=json.dumps(scope.run.replies[key], ensure_ascii=False))
        if scope.run.a2a_calls >= scope.run.max_a2a or (
            scope.role != "central" and scope.delegations >= 1
        ):
            return fail("A2A_BUDGET_EXHAUSTED")
        scope.run.a2a_calls += 1
        scope.delegations += 1
        scope.run.events.append({"source": scope.role, "target": target, "task": request.task})
        token = PENDING.set((target, request))
        try:
            self.input_value = json.dumps(payload, ensure_ascii=False)
            try:
                result = await super().send_to_agent()
            except Exception as exc:
                return Message(
                    text=json.dumps(
                        service_error(target, "SERVICE_EXECUTION_FAILED", scope, exc),
                        ensure_ascii=False,
                    )
                )
            try:
                reply = Reply.model_validate(loads(result.text))
            except (ValueError, TypeError):
                return fail("INVALID_SERVICE_REPLY")
            if reply.service != target:
                return fail("SERVICE_MISMATCH")
            # An unrelated/non-upgraded flow cannot manufacture a successful reply.
            if (reply.status == "success" or reply.results or reply.resolved) and (
                reply.trace.get("request_id") != scope.run.identifier
            ):
                return fail("UNCORRELATED_SERVICE_REPLY")
            value = reply.model_dump(mode="json")
        finally:
            PENDING.reset(token)
        scope.run.replies[key] = value
        return Message(text=json.dumps(value, ensure_ascii=False))
