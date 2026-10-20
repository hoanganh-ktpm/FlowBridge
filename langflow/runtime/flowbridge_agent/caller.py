# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# In-process context survives native Internal A2A's copy_context, without model arguments.
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flowbridge_agent.config import BackendConfig


@dataclass
class Caller:
    execution_id: str
    api_origin: str
    user_token: str = field(repr=False)
    ai_token: str = field(default="", repr=False)
    job_context: str = ""
    knowledge_global: bool = False
    request_ids: set[str] = field(default_factory=set)
    order_ids: set[str] = field(default_factory=set)
    sources: list[dict] = field(default_factory=list)
    warnings: set[str] = field(default_factory=set)
    denied_status: int | None = None
    selected_agents: tuple[str, ...] | None = None
    business_date: str | None = None
    central_span_id: str | None = None
    emit: Callable[[dict], None] | None = field(default=None, repr=False)


CALLER: ContextVar[Caller | None] = ContextVar("flowbridge_authenticated_caller", default=None)


def knowledge_available(config: "BackendConfig") -> bool:
    caller = CALLER.get()
    return (
        bool(caller.ai_token and caller.job_context)
        or bool(caller.knowledge_global and caller.user_token)
        if caller
        else bool(config.ai_token and config.job_context)
    )


def observe(path: str, result: dict) -> None:
    caller = CALLER.get()
    if caller is None:
        return
    if result.get("http_status") in {401, 403}:
        caller.denied_status = result["http_status"]
    if result.get("status") == "error":
        caller.warnings.add(str(result.get("code", "BACKEND_REQUEST_FAILED")))
        return
    if path in {"/internal/v1/tools/retrieve-knowledge", "/api/v1/agent-hub/knowledge/search"}:
        for chunk in result.get("chunks", []):
            reference = chunk.get("reference")
            if isinstance(reference, dict) and reference not in caller.sources:
                caller.sources.append(reference)
        return

    # These identifiers come from API responses, never the model's answer or arguments.
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("request_id") and set(value) & {
                "request_code",
                "requested_by",
                "task_id",
                "proposal_id",
                "prediction_id",
            }:
                caller.request_ids.add(str(value["request_id"]))
            # Historical predictions also expose their PO parent, without po_number.
            if value.get("purchase_order_id"):
                caller.order_ids.add(str(value["purchase_order_id"]))
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item)

    visit(result)
