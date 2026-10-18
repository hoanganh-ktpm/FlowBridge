# SPDX-License-Identifier: GPL-3.0-or-later
"""Additive ver2 adapter over native runtime tool events, not Agent JSON output."""

import json
import re

from lfx.custom import Component
from lfx.io import HandleInput, Output
from lfx.schema.content_types import ToolContent
from lfx.schema.data import Data
from lfx.schema.message import Message

from flowbridge_agent.frontend_response_builder import build_response

SERVICES = {"hoi_du_lieu": "database", "hoi_du_doan": "prediction", "hoi_tri_thuc": "knowledge"}


def envelope(raw):
    # Keep this presentation adapter independent of agent/model imports.
    if isinstance(raw, str):
        raw = raw.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*\n(.*)\n```", raw, flags=re.DOTALL)
        raw = json.loads(fenced.group(1) if fenced else raw)
    if not isinstance(raw, dict) or not isinstance(raw.get("trace"), dict):
        raise ValueError("INVALID_SERVICE_ENVELOPE")
    if not isinstance(raw.get("results", []), list) or len(raw.get("results", [])) > 5:
        raise ValueError("INVALID_RESULTS")
    if not isinstance(raw.get("questions", []), list) or not all(
        isinstance(q, str) for q in raw.get("questions", [])
    ):
        raise ValueError("INVALID_QUESTIONS")
    for receipt in raw.get("results", []):
        if not isinstance(receipt, dict) or not isinstance(receipt.get("tool"), str):
            raise ValueError("INVALID_TOOL_RECEIPT")
        if receipt.get("data") is not None and not isinstance(receipt["data"], dict):
            raise ValueError("INVALID_TOOL_DATA")
        if not isinstance(receipt.get("status"), str):
            raise ValueError("INVALID_TOOL_STATUS")
    return raw


def response_from_message(message: Message) -> dict:
    """Only connect to the existing central Agent response within the same graph.

    Langflow fills ToolContent.output from on_tool_end, after the tool executes.
    Text/structured-response authored by the model never contributes dataset rows.
    This is a trusted internal graph boundary, not a public JSON-ingest endpoint.
    """
    if not isinstance(message, Message) or message.sender != "Machine":
        raise ValueError("CENTRAL_AGENT_MESSAGE_REQUIRED")
    receipts, issues, questions, run_ids = [], [], [], set()
    waiting = False
    blocks = message.content_blocks or []
    for block in blocks:
        if not isinstance(block, ToolContent) or block.name not in SERVICES:
            continue
        if block.error or block.output is None:
            issues.append("TOOL_EVENT_INCOMPLETE")
            continue
        raw = block.output
        # LangChain ToolMessage is preserved by events in memory and serialized
        # to {type: tool, content: ...} by Message. Never parse arbitrary prose.
        if hasattr(raw, "content"):
            raw = raw.content
        if isinstance(raw, dict) and raw.get("type") == "tool" and "content" in raw:
            raw = raw["content"]
        try:
            if len(json.dumps(raw, default=str).encode("utf-8")) > 240000:
                raise ValueError("TOOL_EVENT_TOO_LARGE")
            reply = envelope(raw)
            if reply.get("service") != SERVICES[block.name]:
                raise ValueError("SERVICE_MISMATCH")
            identifier = reply["trace"].get("request_id")
            path = reply["trace"].get("path")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 100:
                raise ValueError("MISSING_RUN_CORRELATION")
            if path not in (["central"], ["central", reply["service"]]):
                raise ValueError("NOT_CENTRAL_TOOL_REPLY")
            run_ids.add(identifier)
            receipts.extend(reply.get("results", []))
            questions.extend(q[:2000] for q in reply.get("questions", []))
            waiting |= reply.get("status") in {"needs_input", "ambiguous", "needs_service"}
            if reply.get("status") in {"error", "unavailable"}:
                issues.append("SERVICE_FAILED")
            elif reply.get("status") == "partial" or reply.get("warnings"):
                issues.append("SERVICE_LIMITATION")
        except (ValueError, TypeError, AttributeError):
            issues.append("INVALID_TOOL_EVENT")
    if len(run_ids) > 1:
        receipts = []
        questions = []
        issues.append("MIXED_RUN_EVENTS")
    output = build_response(
        str(message.text or ""),
        receipts,
        run_id=next(iter(run_ids)) if len(run_ids) == 1 else None,
        issues=list(dict.fromkeys(issues)),
        questions=questions,
    )
    invalid = {"SERVICE_FAILED", "MIXED_RUN_EVENTS", "INVALID_TOOL_EVENT", "TOOL_EVENT_INCOMPLETE"}
    if invalid.intersection(issues) and not output["datasets"]:
        output["status"] = "failed"
        output["error"] = {
            "code": "SERVICE_FAILED",
            "message": "Dịch vụ chưa trả được kết quả.",
            "retryable": False,
        }
    elif waiting and output["status"] != "failed":
        output["status"] = "needs_input"
    from flowbridge_agent.frontend_response_contract import validate_response

    return validate_response(output)


class FrontendResponse(Component):
    name = "FrontendResponse"
    display_name = "JSON cho frontend · ver2"
    description = (
        "Dựng dataset/bảng/biểu đồ từ kết quả công cụ thật của Agent; không gọi thêm model."
    )
    icon = "Braces"
    inputs = [
        HandleInput(
            name="agent_message",
            display_name="Message từ Agent trung tâm",
            input_types=["Message"],
            required=True,
        )
    ]
    outputs = [Output(name="response", display_name="JSON chuẩn frontend", method="build_response")]

    def build_response(self) -> Data:
        result = response_from_message(self.agent_message)
        self.status = {
            "schema_version": result["schema_version"],
            "status": result["status"],
            "datasets": len(result["datasets"]),
            "charts": len(result["charts"]),
        }
        return Data(data=result)
