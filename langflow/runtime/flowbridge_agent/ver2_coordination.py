# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

"""Bounded coordination around native Langflow agents and native Internal A2A."""

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from time import monotonic
from typing import Annotated, Literal
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

from flowbridge_agent.hub_activity import AGENT_LABELS, TOOL_LABELS, error_detail
from flowbridge_agent.hub_activity import outcome as activity_outcome
from flowbridge_agent.tools import ToolResult, failure, tool_result

Service = Literal["database", "prediction", "knowledge"]
EDGES = {
    "central": {"database", "prediction", "knowledge"},
    "prediction": {"database"},
    "knowledge": {"database"},
    "database": set(),
}
TASKS = {
    "database": {
        "resolve_entities",
        "list_catalog",
        "offers",
        "request",
        "order",
        "delivery_orders",
        "history_samples",
        "find_multi_item_orders",
        "find_multi_supplier_requests",
    },
    "prediction": {"rank_suppliers", "score_supplier", "order_risk", "stored_result"},
    "knowledge": {"retrieve"},
}
LIMITS = {"central": 3, "database": 3, "prediction": 2, "knowledge": 2}
ACTION_SERVICE = {
    "catalog_resolve_entities": "database",
    "catalog_list": "database",
    "catalog_read_offers": "database",
    "purchase_get_request": "database",
    "purchase_get_order": "database",
    "supplier_delivery_orders": "database",
    "ml_feature_history": "database",
    "purchase_find_multi_item_orders": "database",
    "purchase_find_multi_supplier_requests": "database",
    "prediction_rank_suppliers": "prediction",
    "prediction_score_supplier": "prediction",
    "prediction_get_order_risk": "prediction",
    "prediction_get_result": "prediction",
    "knowledge_search_supplier": "knowledge",
}
A2A_SERVICE = {"hoi_du_lieu": "database", "hoi_du_doan": "prediction", "hoi_tri_thuc": "knowledge"}
TASK_ACTION = {
    "resolve_entities": "catalog_resolve_entities",
    "list_catalog": "catalog_list",
    "offers": "catalog_read_offers",
    "request": "purchase_get_request",
    "order": "purchase_get_order",
    "delivery_orders": "supplier_delivery_orders",
    "history_samples": "ml_feature_history",
    "find_multi_item_orders": "purchase_find_multi_item_orders",
    "find_multi_supplier_requests": "purchase_find_multi_supplier_requests",
    "rank_suppliers": "prediction_rank_suppliers",
    "score_supplier": "prediction_score_supplier",
    "order_risk": "prediction_get_order_risk",
    "stored_result": "prediction_get_result",
    "retrieve": "knowledge_search_supplier",
}
Status = Literal[
    "success",
    "partial",
    "needs_input",
    "needs_service",
    "ambiguous",
    "not_found",
    "unavailable",
    "error",
]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task: Annotated[str, Field(min_length=1, max_length=60)]
    question: Annotated[str, Field(min_length=1, max_length=6000)]
    business_date: date
    known: dict = Field(default_factory=dict)
    evidence: list[ToolResult] = Field(default_factory=list, max_length=5)

    @field_validator("known", mode="before")
    @classmethod
    def empty_known(cls, value):
        return {} if value is None else value

    @field_validator("evidence", mode="before")
    @classmethod
    def empty_evidence(cls, value):
        # Models commonly use null for an optional collection with no prior results.
        return [] if value is None else value


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: Service
    task: str
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: Service
    status: Status
    resolved: dict = Field(default_factory=dict)
    results: list[ToolResult] = Field(default_factory=list, max_length=5)
    missing: list[str] = Field(default_factory=list, max_length=20)
    questions: list[str] = Field(default_factory=list, max_length=5)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    handoffs: list[Handoff] = Field(default_factory=list, max_length=2)
    trace: dict = Field(default_factory=dict)

    @field_validator("resolved", "trace", mode="before")
    @classmethod
    def empty_objects(cls, value):
        return {} if value is None else value

    @field_validator("results", "missing", "questions", "warnings", "handoffs", mode="before")
    @classmethod
    def empty_lists(cls, value):
        return [] if value is None else value


@dataclass
class Run:
    # Created for one root execution, shared only by its nested in-process A2A calls.
    identifier: str = field(default_factory=lambda: uuid4().hex)
    a2a_calls: int = 0
    model_calls: int = 0
    max_a2a: int = 4
    max_models: int = 14
    replies: dict = field(default_factory=dict)
    tools: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    evidence: set = field(default_factory=set)


@dataclass
class Invocation:
    run: Run
    role: str
    path: tuple[str, ...]
    request: Request | None = None
    calls: int = 0
    delegations: int = 0
    api_calls: int = 0
    receipts: list[dict] = field(default_factory=list)
    child_replies: list[dict] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    span_id: str = field(default_factory=lambda: uuid4().hex)
    activity_status: str | None = None
    activity_error: str = "AGENT_EXECUTION_FAILED"


CURRENT: ContextVar[Invocation | None] = ContextVar("flowbridge_invocation", default=None)
PENDING: ContextVar[tuple[str, Request] | None] = ContextVar("flowbridge_a2a_request", default=None)


def allowed_targets(role: str) -> set[str]:
    from flowbridge_agent.caller import CALLER

    caller = CALLER.get()
    if role == "central" and caller is not None and caller.selected_agents is not None:
        return EDGES[role] & set(caller.selected_agents)
    return EDGES[role]


@contextmanager
def invocation(role: str):
    from flowbridge_agent.caller import CALLER

    parent, pending = CURRENT.get(), PENDING.get()
    caller = CALLER.get()
    if parent is not None:
        if pending is None or pending[0] != role or role in parent.path:
            raise ValueError("INVALID_AGENT_ROUTE")
        scope = Invocation(parent.run, role, (*parent.path, role), pending[1])
    else:
        scope = Invocation(Run(), role, (role,))
        if caller and caller.selected_agents is not None:
            scope.run.identifier = caller.execution_id
            caller.central_span_id = scope.span_id
    if scope.request is not None:
        scope.receipts = [r.model_dump(mode="json") for r in scope.request.evidence]
    token = CURRENT.set(scope)
    pending_token = PENDING.set(None)
    started = monotonic()
    data = {
        "span_id": scope.span_id,
        "parent_span_id": parent.span_id if parent else None,
        "agent": role,
        "invoked_by": parent.role if parent else None,
        "role": "coordinator"
        if parent is None
        else "primary"
        if parent.role == "central"
        else "dependency",
        "label": AGENT_LABELS[role],
    }
    outcome = "success"
    try:
        if caller and caller.emit:
            caller.emit({"type": "agent.started", "data": data})
        yield scope
        if scope.activity_status:
            outcome = scope.activity_status
        elif scope.blocked:
            outcome = "failed"
            scope.activity_error = scope.blocked[0]
        elif any(
            r.get("status") in {"error", "unavailable", "partial"} for r in scope.child_replies
        ):
            outcome = "failed"
            scope.activity_error = "DEPENDENCY_FAILED"
        elif any(
            r.get("questions") or r.get("status") in {"needs_input", "ambiguous"}
            for r in scope.child_replies
        ):
            outcome = "needs_input"
    except BaseException:
        outcome = "failed"
        raise
    finally:
        if caller and caller.emit:
            caller.emit(
                {
                    "type": "agent.completed",
                    "data": {
                        **data,
                        "outcome": outcome,
                        "duration_ms": max(0, int((monotonic() - started) * 1000)),
                        **(
                            {"error": error_detail(scope.activity_error, scope.run.identifier)}
                            if outcome == "failed"
                            else {}
                        ),
                    },
                }
            )
        PENDING.reset(pending_token)
        CURRENT.reset(token)


def loads(value):
    if isinstance(value, str):
        if len(value) > 60000:
            raise ValueError("PAYLOAD_TOO_LARGE")
        # Native Parser.Stringify serializes Data as a fenced JSON block. Accept
        # that single transport wrapper, never extract JSON from arbitrary prose.
        value = value.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*\n(.*)\n```", value, flags=re.DOTALL)
        if fenced:
            value = fenced.group(1)
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("INVALID_JSON_OBJECT")
    return value


def stamp(reply: Reply, scope: Invocation) -> dict:
    value = reply.model_dump(mode="json")
    value["trace"] = {
        "request_id": scope.run.identifier,
        "path": list(scope.path),
        "a2a_calls": scope.run.a2a_calls,
        "model_calls": scope.run.model_calls,
    }
    return value


def stopped(service: Service, code: str) -> dict:
    return Reply(service=service, status="unavailable", warnings=[code]).model_dump(mode="json")


def collect(scope: Invocation, receipt: dict):
    encoded = json.dumps(receipt, sort_keys=True, ensure_ascii=False)
    scope.run.evidence.add(encoded)
    if not any(
        json.dumps(r, sort_keys=True, ensure_ascii=False) == encoded for r in scope.receipts
    ):
        scope.receipts.append(deepcopy(receipt))


def resolved_from(receipts: list[dict]) -> dict:
    resolved = {}
    for result in receipts:
        if result["status"] in {"error", "unavailable"} or not isinstance(result["data"], dict):
            continue
        data, action = result["data"], result["tool"]
        if action in {"catalog_resolve_entities", "catalog_read_offers"}:
            resolved.update(data.get("resolved", {}))
        elif action == "purchase_get_order" and "purchase_order_id" in data:
            resolved["order_id"] = data["purchase_order_id"]
            resolved.update(
                {
                    k: data[k]
                    for k in ("supplier_id", "supplier_code", "supplier_name")
                    if data.get(k)
                }
            )
        elif action == "purchase_get_request" and "request_id" in data:
            resolved["request_id"] = data["request_id"]
        elif action.startswith("prediction_"):
            resolved.update(result["input"])
    return resolved


def prediction_preflight(request: Request, scope: Invocation) -> dict | None:
    if request.task not in {"rank_suppliers", "score_supplier"}:
        return None
    missing = [
        key
        for key in ("quantity", "requested_delivery_date")
        if request.known.get(key) is None or str(request.known[key]).strip() == ""
    ]
    if not missing:
        return None
    product = request.known.get("product_id") or request.known.get("product")
    receipt = None
    for result in reversed(scope.receipts):
        if result["tool"] not in {"catalog_resolve_entities", "catalog_read_offers"}:
            continue
        resolved = resolved_from([result])
        selectors = [result["input"].get("product")]
        selectors.extend(
            resolved.get(key) for key in ("product_id", "product_code", "product_name")
        )
        if (
            resolved.get("product_id")
            and product
            and any(
                str(product).strip().casefold() == str(value).strip().casefold()
                for value in selectors
                if value is not None
            )
        ):
            receipt = result
            break
    reply = Reply(service="prediction", status="needs_input", missing=missing)
    if product and receipt is None:
        from flowbridge_agent.caller import CALLER

        caller = CALLER.get()
        if (
            caller
            and caller.selected_agents is not None
            and "database" not in caller.selected_agents
        ):
            # In Hub mode ML resolves its own Data dependency; central cannot call unselected Data.
            return None
        reply.status = "needs_service"
        reply.handoffs = [
            Handoff(
                service="database",
                task="resolve_entities",
                reason="Tra sản phẩm bằng tên/mã đã có trước khi hỏi số lượng/ngày nhận. "
                "Giữ nguyên điều kiện đã biết; chưa gọi ML khi còn thiếu thông tin.",
            )
        ]
    else:
        if receipt:
            reply.resolved = resolved_from([receipt])
            reply.results = [ToolResult.model_validate(receipt)]
        else:
            reply.missing.insert(0, "product")
        labels = {
            "product": "tên hoặc mã sản phẩm",
            "quantity": "số lượng cần mua",
            "requested_delivery_date": "ngày cần nhận hàng",
        }
        reply.questions = [
            "Bạn cho biết " + ", ".join(labels[key] for key in reply.missing) + " nhé?"
        ]
    return stamp(reply, scope)


def service_error(service: Service, code: str, scope: Invocation, exc: Exception) -> dict:
    # Only expose known codes and exception types, never provider text or credentials.
    if str(exc) in {
        "MODEL_BUDGET_EXHAUSTED",
        "A2A_BUDGET_EXHAUSTED",
        "APPROVED_LIVE_TEST_BUDGET_EXHAUSTED",
        "SERVICE_MISMATCH",
    }:
        code = str(exc)
    scope.run.events.append({"target": service, "code": code, "error_type": type(exc).__name__})
    return stamp(Reply(service=service, status="error", warnings=[code]), scope)


def finish_reply(raw: dict, scope: Invocation) -> dict:
    # The LLM can select tools and explain missing inputs; actual evidence comes from handlers.
    try:
        reply = Reply.model_validate(raw)
        if reply.service != scope.role:
            raise ValueError("SERVICE_MISMATCH")
    except (ValueError, TypeError):
        from flowbridge_agent.ver2_catalog_reply import catalog_receipt_reply

        catalog = catalog_receipt_reply(raw, scope)
        if catalog is None:
            reply = Reply(service=scope.role, status="error", warnings=["SERVICE_OUTPUT_INVALID"])
        else:
            reply = Reply.model_validate(catalog)
            scope.run.events.append(
                {
                    "target": "database",
                    "code": "SERVICE_OUTPUT_INVALID",
                    "recovered_by": "VERIFIED_CATALOG_RECEIPT",
                }
            )
    actual = scope.receipts[-5:]
    allowed = {json.dumps(r, sort_keys=True, ensure_ascii=False) for r in scope.receipts}
    if any(
        json.dumps(r.model_dump(mode="json"), sort_keys=True, ensure_ascii=False) not in allowed
        for r in reply.results
    ):
        reply.status = "error"
        reply.warnings.append("UNSUPPORTED_TOOL_RESULT")
    reply.results = [ToolResult.model_validate(r) for r in actual]
    reply.resolved = resolved_from(scope.receipts)
    for handoff in reply.handoffs:
        if handoff.service == scope.role or handoff.task not in TASKS[handoff.service]:
            reply.status = "error"
            reply.warnings.append("INVALID_DEPENDENCY")
            reply.handoffs = []
            break
    for child in scope.child_replies:
        if child["status"] in {"ambiguous", "needs_input", "not_found", "unavailable", "error"}:
            reply.missing = list(dict.fromkeys([*reply.missing, *child["missing"]]))
            reply.questions = list(dict.fromkeys([*reply.questions, *child["questions"]]))[:5]
            reply.warnings.extend(w for w in child["warnings"] if w not in reply.warnings)
            if reply.status == "success":
                reply.status = child["status"]
    if scope.blocked:
        reply.warnings = list(dict.fromkeys([*reply.warnings, *scope.blocked]))[:20]
        if reply.status == "success":
            reply.status = "partial" if actual else "unavailable"
    if reply.status == "success" and not actual:
        reply.status = "needs_input" if reply.missing else "error"
        reply.warnings.append("NO_TOOL_EVIDENCE")
    own = [r for r in actual if ACTION_SERVICE.get(r["tool"]) == scope.role]
    if reply.status == "success" and own and any(r["status"] == "error" for r in own):
        reply.status = "error"
    if reply.status == "success" and own and any(r["status"] not in {"success"} for r in own):
        reply.status = "partial"
    if scope.role in {"prediction", "knowledge"} and reply.status == "success" and not own:
        reply.status = "partial"
        reply.warnings.append("DEPENDENCY_RESOLVED_BUT_TASK_NOT_EXECUTED")
    return stamp(reply, scope)


class CoordinationMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        from flowbridge_agent.caller import CALLER

        scope = CURRENT.get()
        if scope is None or scope.run.model_calls >= scope.run.max_models:
            if scope:
                scope.blocked.append("MODEL_BUDGET_EXHAUSTED")
            raise ValueError("MODEL_BUDGET_EXHAUSTED")
        scope.run.model_calls += 1
        caller = CALLER.get()
        if scope.role == "central" and caller and caller.selected_agents is not None:
            request = request.override(
                tools=[
                    t
                    for t in request.tools
                    if A2A_SERVICE.get(getattr(t, "name", "")) in allowed_targets("central")
                ]
            )
        if scope.request is not None:
            actions = {TASK_ACTION[scope.request.task]}
            if scope.role in {"prediction", "knowledge"}:
                actions.add("hoi_du_lieu")
                if any(c["status"] not in {"success"} for c in scope.child_replies):
                    actions.discard(TASK_ACTION[scope.request.task])
            # Present only task-relevant tools, rather than ask the model to
            # choose among unrelated APIs with overlapping descriptions.
            request = request.override(
                tools=[
                    t
                    for t in request.tools
                    if getattr(t, "action", getattr(t, "name", None)) in actions
                ]
            )
        # Native create_agent passes these options into bind_tools; no extra LLM call.
        if isinstance(request.model, ChatOpenAI) and request.tools:
            request = request.override(
                model_settings={**request.model_settings, "parallel_tool_calls": False}
            )
        return await handler(request)

    async def awrap_tool_call(self, request, handler):
        from flowbridge_agent.caller import CALLER

        caller, scope = CALLER.get(), CURRENT.get()
        name = request.tool_call["name"]
        target = A2A_SERVICE.get(name)
        if not (caller and caller.emit and scope and target):
            return await self._awrap_tool_call(request, handler)
        started = monotonic()
        data = {
            "span_id": uuid4().hex,
            "parent_span_id": scope.span_id,
            "agent": target,
            "tool": name,
            "label": TOOL_LABELS[name],
        }
        caller.emit({"type": "tool.started", "data": data})
        outcome, code = "failed", "HANDOFF_FAILED"
        try:
            response = await self._awrap_tool_call(request, handler)
            payload = loads(response.content)
            outcome = activity_outcome(payload.get("status", "error"))
            code = (payload.get("error") or {}).get("code") or next(
                iter(payload.get("warnings", [])), "HANDOFF_FAILED"
            )
            return response
        finally:
            caller.emit(
                {
                    "type": "tool.completed",
                    "data": {
                        **data,
                        "outcome": outcome,
                        "duration_ms": max(0, int((monotonic() - started) * 1000)),
                        **(
                            {"error": error_detail(code, scope.run.identifier)}
                            if outcome == "failed"
                            else {}
                        ),
                    },
                }
            )

    async def _awrap_tool_call(self, request, handler):
        scope = CURRENT.get()
        call = request.tool_call
        name = call["name"]
        action = getattr(request.tool, "action", name)
        key = json.dumps([scope.role if scope else None, name, call["args"]], sort_keys=True)

        def response(payload):
            return ToolMessage(
                content=json.dumps(payload, ensure_ascii=False), name=name, tool_call_id=call["id"]
            )

        if scope is None:
            return response(tool_result(name, {}, failure("AGENT_SCOPE_REQUIRED")))
        target = A2A_SERVICE.get(name)
        if (
            target is None
            and scope.role in {"prediction", "knowledge"}
            and any(c["status"] != "success" for c in scope.child_replies)
        ):
            scope.blocked.append("DEPENDENCY_NOT_RESOLVED")
            return response(tool_result(action, {}, failure("DEPENDENCY_NOT_RESOLVED")))
        if (
            scope.request is not None
            and target is None
            and action != TASK_ACTION[scope.request.task]
        ):
            scope.blocked.append("TOOL_OUTSIDE_TASK")
            return response(tool_result(action, {}, failure("TOOL_OUTSIDE_TASK")))
        if (target is not None and target not in allowed_targets(scope.role)) or (
            target is None and ACTION_SERVICE.get(action) != scope.role
        ):
            scope.blocked.append("TOOL_OUTSIDE_SERVICE")
            return response(tool_result(name, {}, failure("TOOL_OUTSIDE_SERVICE")))
        if target == "prediction":
            try:
                handoff = Request.model_validate(loads(call["args"].get("input_value")))
            except (ValueError, TypeError) as exc:
                return response(service_error(target, "INVALID_HANDOFF_REQUEST", scope, exc))
            preflight = prediction_preflight(handoff, scope)
            if preflight is not None:
                return response(preflight)
        if key in scope.run.tools:
            payload = deepcopy(scope.run.tools[key])
        else:
            if scope.calls >= LIMITS[scope.role]:
                scope.blocked.append("TOOL_BUDGET_EXHAUSTED")
                return response(tool_result(name, {}, failure("TOOL_BUDGET_EXHAUSTED")))
            if (
                target is None
                and scope.role in {"prediction", "knowledge"}
                and scope.api_calls >= 1
            ):
                scope.blocked.append("API_BUDGET_EXHAUSTED")
                return response(tool_result(action, {}, failure("API_BUDGET_EXHAUSTED")))
            scope.calls += 1
            if target is None:
                scope.api_calls += 1
            from flowbridge_agent.caller import CALLER

            caller = CALLER.get()
            started = monotonic()
            activity = {
                "span_id": uuid4().hex,
                "parent_span_id": scope.span_id,
                "agent": scope.role,
                "tool": action,
                "label": TOOL_LABELS.get(action, action),
            }
            if target is None and caller and caller.emit:
                caller.emit({"type": "tool.started", "data": activity})
            try:
                message = await handler(request)
            except Exception as exc:
                payload = (
                    service_error(target, "SERVICE_EXECUTION_FAILED", scope, exc)
                    if target
                    else tool_result(action, {}, failure("TOOL_CALL_FAILED"))
                )
            else:
                try:
                    payload = loads(message.content)
                    if target:
                        parsed = Reply.model_validate(payload)
                        if parsed.service != target:
                            raise ValueError("SERVICE_MISMATCH")
                        payload = parsed.model_dump(mode="json")
                    else:
                        parsed = ToolResult.model_validate(payload)
                        if parsed.tool != action:
                            raise ValueError("TOOL_RESULT_MISMATCH")
                        payload = parsed.model_dump(mode="json")
                except (ValueError, TypeError) as exc:
                    payload = (
                        service_error(target, "INVALID_SERVICE_REPLY", scope, exc)
                        if target
                        else tool_result(action, {}, failure("TOOL_CALL_FAILED"))
                    )
            if target is None and caller and caller.emit:
                outcome = activity_outcome(payload.get("status", "error"))
                caller.emit(
                    {
                        "type": "tool.completed",
                        "data": {
                            **activity,
                            "outcome": outcome,
                            "duration_ms": max(0, int((monotonic() - started) * 1000)),
                            **(
                                {
                                    "error": error_detail(
                                        (payload.get("error") or {}).get("code", "TOOL_FAILED"),
                                        scope.run.identifier,
                                    )
                                }
                                if outcome == "failed"
                                else {}
                            ),
                        },
                    }
                )
            scope.run.tools[key] = deepcopy(payload)
        if target:
            scope.child_replies.append(payload)
            for receipt in payload["results"]:
                collect(scope, receipt)
        else:
            collect(scope, payload)
        return response(payload)
