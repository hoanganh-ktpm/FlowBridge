# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

"""Additive protocol nodes around existing ver2 tools; no model or transport of their own."""

import json
from copy import deepcopy
from typing import Any

from langchain_core.tools import BaseTool
from lfx.custom import Component
from lfx.field_typing import Tool
from lfx.io import DropdownInput, HandleInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message
from pydantic import BaseModel, Field

from flowbridge_agent.ver2_coordination import (
    CURRENT,
    TASKS,
    Reply,
    Request,
    allowed_targets,
    collect,
    loads,
    resolved_from,
    stamp,
)
from flowbridge_agent.ver2_entity_dependency import complete_entity_dependency, scope_entity_request

NAMES = {"database": "hoi_du_lieu", "prediction": "hoi_du_doan", "knowledge": "hoi_tri_thuc"}


class ProtocolInput(BaseModel):
    input_value: str = Field(
        description="JSON A2A task/question/business_date/known; evidence do runtime giữ."
    )


def matching_receipts(scope, known: dict) -> list[dict]:
    """Only forward receipts matching the requested identities, within this execution."""
    matches = []
    for receipt in scope.receipts:
        resolved = resolved_from([receipt])
        if not resolved:
            continue
        relevant = False
        for stem in ("product", "supplier", "order", "request"):
            selector = known.get(stem + "_id") or known.get(stem)
            if not selector:
                continue
            candidates = [receipt["input"].get(stem), receipt["input"].get("identifier")]
            candidates += [resolved.get(stem + suffix) for suffix in ("_id", "_code", "_name")]
            found = any(
                str(selector).strip().casefold() == str(candidate).strip().casefold()
                for candidate in candidates
                if candidate is not None
            )
            if not found:
                relevant = False
                break
            relevant = True
        if relevant:
            matches.append(deepcopy(receipt))
    return matches[-5:]


def normalize_request(value: str, target: str, scope) -> Request:
    raw = deepcopy(loads(value))
    # Serialized evidence authored by a model is never the source of truth.
    raw["evidence"] = []
    request = Request.model_validate(raw)
    parent = scope.request
    if scope.role in {"prediction", "knowledge"} and target == "database":
        if parent is None:
            raise ValueError("PROTOCOL_PARENT_REQUIRED")
        request.business_date = parent.business_date
        request.known = deepcopy(parent.known)
        # A dependency uses a Data task, never the parent's ML/RAG task.
        if parent.task == "order_risk":
            request.task = "order"
        elif parent.task in {"rank_suppliers", "score_supplier", "retrieve"}:
            request.task = "resolve_entities"
        else:
            raise ValueError("PROTOCOL_TASK_NOT_ALLOWED")
    if request.task not in TASKS[target]:
        raise ValueError("PROTOCOL_TASK_NOT_ALLOWED")
    scope_entity_request(request, target, scope)
    receipts = matching_receipts(scope, request.known)
    request.evidence = Reply(service=target, status="partial", results=receipts).results
    request.known.update(resolved_from(receipts))
    return request


class ProtocolTool(BaseTool):
    args_schema: type[BaseModel] = ProtocolInput
    original: BaseTool = Field(exclude=True, repr=False)
    caller_service: str
    target_service: str

    @property
    def action(self) -> str:
        scope = CURRENT.get()
        if (
            scope
            and scope.role in {"prediction", "knowledge"}
            and self.target_service == "database"
        ):
            # The existing task middleware consults action before each model call.
            # After a dependency reply, let the model finish or call its own API.
            if scope.child_replies:
                return "dependency_completed"
            if scope.request:
                resolved = resolved_from(matching_receipts(scope, scope.request.known))
                required = {
                    "rank_suppliers": ("product_id",),
                    "score_supplier": ("product_id", "supplier_id"),
                    "order_risk": ("order_id",),
                    "retrieve": ("supplier_id",),
                }.get(scope.request.task, ())
                if required and all(resolved.get(key) for key in required):
                    return "dependency_completed"
        return self.name

    def _run(self, **kwargs: Any):
        raise NotImplementedError("Use asynchronous tools")

    async def _arun(self, input_value: str):
        scope = CURRENT.get()
        try:
            if scope is None or scope.role != self.caller_service:
                raise ValueError("PROTOCOL_SCOPE_REQUIRED")
            if self.target_service not in allowed_targets(scope.role):
                raise ValueError("PROTOCOL_ROUTE_BLOCKED")
            request = normalize_request(input_value, self.target_service, scope)
            raw = await self.original.ainvoke(
                {"input_value": json.dumps(request.model_dump(mode="json"), ensure_ascii=False)}
            )
            if isinstance(raw, Message):
                raw = raw.text
            reply = Reply.model_validate(loads(raw))
            if reply.service != self.target_service:
                raise ValueError("PROTOCOL_REPLY_SERVICE_MISMATCH")
            if (reply.results or reply.resolved or reply.status == "success") and (
                reply.trace.get("request_id") != scope.run.identifier
            ):
                raise ValueError("PROTOCOL_REPLY_UNCORRELATED")
            observed = scope.run.evidence
            for result in reply.results:
                receipt = result.model_dump(mode="json")
                if json.dumps(receipt, sort_keys=True, ensure_ascii=False) not in observed:
                    raise ValueError("PROTOCOL_REPLY_UNTRUSTED_EVIDENCE")
                collect(scope, receipt)
            reply = complete_entity_dependency(reply, request, scope)
            if scope.role in {"prediction", "knowledge"} and self.target_service == "database":
                # Keep the complete receipts in runtime. The model only needs IDs,
                # names, missing inputs and (for ambiguity) the actual choices.
                if reply.status == "success":
                    reply.results = []
            return reply.model_dump(mode="json")
        except Exception as exc:
            allowed = {
                "PROTOCOL_SCOPE_REQUIRED",
                "PROTOCOL_ROUTE_BLOCKED",
                "PROTOCOL_PARENT_REQUIRED",
                "PROTOCOL_TASK_NOT_ALLOWED",
                "PROTOCOL_REPLY_SERVICE_MISMATCH",
                "PROTOCOL_REPLY_UNCORRELATED",
                "PROTOCOL_REPLY_UNTRUSTED_EVIDENCE",
            }
            code = (
                str(exc)
                if isinstance(exc, ValueError) and str(exc) in allowed
                else "PROTOCOL_INVALID_MESSAGE"
            )
            reply = Reply(service=self.target_service, status="error", warnings=[code])
            return stamp(reply, scope) if scope else reply.model_dump(mode="json")


class A2AProtocolAdapter(Component):
    name = "A2AProtocolAdapter"
    display_name = "Chuẩn hóa giao tiếp A2A"
    description = (
        "Chuẩn hóa task/JSON, giữ chứng cứ bằng runtime và đóng nhánh Data đã xong; "
        "không gọi model."
    )
    icon = "Braces"
    inputs = [
        HandleInput(
            name="source_tools",
            display_name="A2A hiện có",
            input_types=["Tool"],
            is_list=True,
            required=True,
        ),
        DropdownInput(
            name="caller_service",
            display_name="Bên gọi",
            options=["central", "prediction", "knowledge"],
            value="central",
        ),
        DropdownInput(
            name="target_service", display_name="Bên nhận", options=list(NAMES), value="database"
        ),
    ]
    outputs = [Output(name="tools", display_name="Công cụ đã chuẩn hóa", method="build_tools")]

    def build_tools(self) -> list[Tool]:
        sources = self.source_tools or []
        if len(sources) != 1 or not isinstance(sources[0], BaseTool):
            raise ValueError("Expected exactly one existing A2A tool")
        source = sources[0]
        description = (
            source.description
            + "\nRuntime chuẩn hóa JSON và giữ evidence; không chép lại ToolResult."
        )
        if self.caller_service != "central":
            description += (
                " Chỉ giải quyết định danh; nhận resolved xong thì dùng API chuyên môn, "
                "không gọi lại Data."
            )
        return [
            ProtocolTool(
                name=NAMES[self.target_service],
                description=description,
                original=source,
                caller_service=self.caller_service,
                target_service=self.target_service,
            )
        ]


class A2AReplyEnvelope(Component):
    name = "A2AReplyEnvelope"
    display_name = "Kiểm tra JSON trả về A2A"
    description = (
        "Bỏ khung JSON, kiểm schema/service/handoff, giữ nguyên lỗi và chứng cứ; không gọi model."
    )
    icon = "ShieldCheck"
    inputs = [
        HandleInput(
            name="input_value",
            display_name="Phản hồi từ Parser cũ",
            input_types=["Message", "Data"],
            required=True,
        ),
        DropdownInput(
            name="service", display_name="Service", options=list(NAMES), value="database"
        ),
    ]
    outputs = [Output(name="envelope", display_name="JSON đã kiểm tra", method="validate_reply")]

    def validate_reply(self) -> Data:
        try:
            raw = (
                self.input_value.text
                if isinstance(self.input_value, Message)
                else self.input_value.data
            )
            reply = Reply.model_validate(loads(raw))
            if reply.service != self.service:
                raise ValueError("Service mismatch")
            if any(
                h.service == self.service or h.task not in TASKS[h.service] for h in reply.handoffs
            ):
                raise ValueError("Invalid handoff")
        except (ValueError, TypeError, AttributeError):
            reply = Reply(
                service=self.service, status="error", warnings=["PROTOCOL_INVALID_REPLY_ENVELOPE"]
            )
        # Type Convert (native) will emit this exact JSON string as a Message.
        return Data(data={"text": json.dumps(reply.model_dump(mode="json"), ensure_ascii=False)})
