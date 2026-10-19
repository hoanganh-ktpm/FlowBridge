# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from copy import deepcopy
from datetime import datetime


def span_state(events: list[dict]) -> tuple[dict, set]:
    started, completed = {}, set()
    for event in events:
        if event["type"] in {"agent.started", "tool.started"}:
            started[event["data"]["span_id"]] = event
        elif event["type"] in {"agent.completed", "tool.completed"}:
            completed.add(event["data"]["span_id"])
    return started, completed


def validate_activity(event: dict, events: list[dict], selected: list[str]) -> None:
    started, completed = span_state(events)
    data, kind = event["data"], event["type"]
    span = data["span_id"]
    if kind.endswith(".completed"):
        original = started.get(span)
        if (
            not original
            or span in completed
            or original["type"].split(".")[0] != kind.split(".")[0]
        ):
            raise ValueError("INVALID_SPAN_COMPLETION")
        if any(data.get(k) != v for k, v in original["data"].items()):
            raise ValueError("SPAN_IDENTITY_CHANGED")
        if data["outcome"] == "failed" and "error" not in data:
            raise ValueError("MISSING_STEP_ERROR")
        return
    if span in started:
        raise ValueError("DUPLICATE_SPAN")
    parent = started.get(data.get("parent_span_id"))
    if kind == "agent.started" and data["agent"] == "central":
        if (
            data["parent_span_id"] is not None
            or data["role"] != "coordinator"
            or data["invoked_by"] is not None
        ):
            raise ValueError("INVALID_COORDINATOR")
        if any(e["data"].get("role") == "coordinator" for e in started.values()):
            raise ValueError("DUPLICATE_COORDINATOR")
        return
    if not parent or parent["type"] != "agent.started":
        raise ValueError("MISSING_PARENT_AGENT")
    parent_agent = parent["data"]["agent"]
    if kind == "tool.started":
        # The deterministic output node may fetch statistics after central finished.
        enrichment = (
            parent_agent == "central"
            and data["agent"] == "database"
            and data["tool"] == "supplier_delivery_statistics"
        )
        handoff_target = {
            "hoi_du_lieu": "database",
            "hoi_du_doan": "prediction",
            "hoi_tri_thuc": "knowledge",
        }.get(data["tool"])
        handoff = handoff_target == data["agent"] and (
            parent_agent == "central"
            or parent_agent in {"prediction", "knowledge"}
            and handoff_target == "database"
        )
        if data["agent"] != parent_agent and not (enrichment or handoff):
            raise ValueError("WRONG_TOOL_AGENT")
        return
    if data["invoked_by"] != parent_agent:
        raise ValueError("WRONG_CALLER_AGENT")
    if parent_agent == "central":
        if data["agent"] not in selected or data["role"] != "primary":
            raise ValueError("UNSELECTED_PRIMARY_AGENT")
    elif not (
        parent_agent in {"prediction", "knowledge"}
        and data["agent"] == "database"
        and data["role"] == "dependency"
    ):
        raise ValueError("INVALID_DEPENDENCY_AGENT")


def interrupted_spans(events: list[dict], run_id: str, status: str, now: datetime) -> list[dict]:
    started, completed = span_state(events)
    result = []
    for span, event in reversed(started.items()):
        if span in completed:
            continue
        data = deepcopy(event["data"])
        data.update(
            outcome="cancelled" if status == "cancelled" else "failed",
            duration_ms=max(
                0, int((now - datetime.fromisoformat(event["occurred_at"])).total_seconds() * 1000)
            ),
        )
        if data["outcome"] == "failed":
            data["error"] = {
                "code": "STEP_INTERRUPTED",
                "retryable": False,
                "trace_id": run_id,
                "message": "Lượt chạy đã dừng trước khi xác nhận kết quả bước này.",
            }
        result.append({"type": event["type"].replace(".started", ".completed"), "data": data})
    return result
