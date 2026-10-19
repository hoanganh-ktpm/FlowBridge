# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# ASGI adapter around upstream Langflow; no dependency source changes or token in graph data.
import json
import secrets
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    from fastapi import FastAPI

from flowbridge_agent.caller import CALLER, Caller
from flowbridge_agent.config import BackendConfig


class CallerMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        secret: str,
        api_origin: str,
        flow_id: str = "flowbridge-ver2-central",
    ):
        if len(secret) < 32:
            raise ValueError("Configure a bridge secret with at least 32 characters")
        BackendConfig(origin=api_origin)
        self.app, self.secret, self.api_origin, self.flow_id = app, secret, api_origin, flow_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        private = [
            (k.lower(), v) for k, v in scope["headers"] if k.lower().startswith(b"x-flowbridge-")
        ]
        if not private:
            return await self.app(scope, receive, send)
        headers = dict(private)
        if scope["path"] == "/_ops/flowbridge":
            valid_probe = (
                len(headers) == len(private)
                and scope["method"] == "GET"
                and secrets.compare_digest(
                    headers.get(b"x-flowbridge-bridge-secret", b""), self.secret.encode()
                )
            )
            hub = headers.get(b"x-flowbridge-protocol") == b"agent-hub-v1"
            result = {
                "protocol": "agent-hub-v1" if hub else "flowbridge-caller-v1",
                "flow_id": self.flow_id,
            }
            return await JSONResponse(result if valid_probe else {}, 200 if valid_probe else 403)(
                scope, receive, send
            )
        valid = (
            len(headers) == len(private)
            and scope["method"] == "POST"
            and scope["path"] == "/api/v1/run/" + self.flow_id
            and not scope.get("query_string")
            and secrets.compare_digest(
                headers.get(b"x-flowbridge-bridge-secret", b""), self.secret.encode()
            )
        )
        if not valid:
            return await JSONResponse({"detail": "Bridge request rejected"}, 403)(
                scope, receive, send
            )
        hub = headers.get(b"x-flowbridge-protocol") == b"agent-hub-v1"
        if b"x-flowbridge-protocol" in headers and not hub:
            return await JSONResponse({"detail": "Unsupported protocol"}, 422)(scope, receive, send)
        raw = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            raw.extend(event.get("body", b""))
            if len(raw) > (262144 if hub else 20000):
                return await JSONResponse({"detail": "Invalid bridge body"}, 413)(
                    scope, receive, send
                )
            if not event.get("more_body"):
                break
        try:
            body = json.loads(raw)
            if set(body) != {"input_value", "session_id", "input_type", "output_type"}:
                raise ValueError
            UUID(body["session_id"])
            if (
                body["input_type"] != "chat"
                or body["output_type"] != "chat"
                or not isinstance(body["input_value"], str)
                or not 1 <= len(body["input_value"]) <= (100000 if hub else 4000)
            ):
                raise ValueError
            user_token = headers[b"x-flowbridge-access-token"].decode("ascii")
            ai_token = headers.get(b"x-flowbridge-ai-token", b"").decode("ascii")
            job = headers.get(b"x-flowbridge-job-context", b"").decode("ascii")
            bound_request = headers.get(b"x-flowbridge-request-context", b"").decode("ascii")
            if bound_request:
                UUID(bound_request)
            if not user_token or bool(ai_token) != bool(job):
                raise ValueError
            if job:
                UUID(job)
            selected, business_date = None, None
            if hub:
                selected = json.loads(headers[b"x-flowbridge-selected-agents"])
                business_date = headers[b"x-flowbridge-business-date"].decode("ascii")
                date.fromisoformat(business_date)
                if (
                    not isinstance(selected, list)
                    or len(selected) > 3
                    or any(not isinstance(x, str) for x in selected)
                    or len(set(selected)) != len(selected)
                    or not set(selected) <= {"database", "prediction", "knowledge"}
                ):
                    raise ValueError
        except (ValueError, TypeError, KeyError, UnicodeError):
            return await JSONResponse({"detail": "Invalid bridge request"}, 422)(
                scope, receive, send
            )
        caller = Caller(body["session_id"], self.api_origin, user_token, ai_token, job)
        if hub:
            caller.selected_agents = tuple(selected)
            caller.business_date = business_date
            body["output_type"] = "any"
            body["output_component"] = "FrontendResponse-ver2-central"
        if bound_request:
            caller.request_ids.add(bound_request)
            # Business context is safe to show to the agent; authorization credentials are not.
            body["input_value"] = (
                "Ngữ cảnh PR do backend xác minh: "
                + bound_request
                + "\nCâu hỏi người dùng: "
                + body["input_value"]
            )
        if not job:
            caller.warnings.add("RAG_REQUIRES_AUTHORIZED_ANALYSIS_JOB")
        # Fresh native execution session, no native cross-turn memory or history replay.
        body["tweaks"] = {
            "ChatInput-v2-central": {"should_store_message": False},
            "ChatOutput-v2-central": {"should_store_message": False},
            "Agent-v2-central": {"n_messages": 0},
        }
        payload = json.dumps(
            {"input_request": body, "context": {"flowbridge_execution": caller.execution_id}}
        ).encode()
        clean_scope = {
            **scope,
            "headers": [
                (k, v)
                for k, v in scope["headers"]
                if not k.lower().startswith(b"x-flowbridge-") and k.lower() != b"content-length"
            ]
            + [(b"content-length", str(len(payload)).encode())],
        }
        consumed = False

        async def receive_body() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return await receive()

        if hub:
            from flowbridge_agent.hub_serving import stream_hub

            return await stream_hub(
                self.app, caller, scope, clean_scope, receive, receive_body, send
            )

        response = bytearray()
        status = 500

        async def capture(event: Message) -> None:
            nonlocal status
            if event["type"] == "http.response.start":
                status = event["status"]
            elif event["type"] == "http.response.body":
                response.extend(event.get("body", b""))

        token = CALLER.set(caller)
        try:
            await self.app(clean_scope, receive_body, capture)
        finally:
            CALLER.reset(token)
        if status != 200 or caller.denied_status:
            code = caller.denied_status or (status if status in {401, 403, 429, 504} else 503)
            denied = (
                {"X-FlowBridge-Tool-Denied": str(caller.denied_status)}
                if caller.denied_status
                else {}
            )
            return await JSONResponse({"detail": "Flow execution failed"}, code, headers=denied)(
                scope, receive, send
            )
        try:
            result = json.loads(response)
            messages = [
                item["results"]["message"]["text"]
                for group in result["outputs"]
                for item in group["outputs"]
                if isinstance(item.get("results", {}).get("message", {}).get("text"), str)
            ]
            if len(messages) != 1 or not messages[0].strip() or len(messages[0]) > 6000:
                raise ValueError
            result = {
                "answer": messages[0],
                "sources": caller.sources,
                "warnings": sorted(caller.warnings),
                "needs_clarification": False,
                "request_ids": sorted(caller.request_ids),
                "order_ids": sorted(caller.order_ids),
            }
        except (ValueError, KeyError, TypeError, AttributeError):
            return await JSONResponse({"detail": "Invalid flow output"}, 503)(scope, receive, send)
        await JSONResponse(result)(scope, receive, send)


def create_app() -> "FastAPI":
    # Called after launcher loads the same ignored agent.env used by the editor.
    import os

    from flowbridge_agent.config import local_values

    values = local_values()
    if len(values.get("FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET", "")) < 32:
        raise ValueError("Configure FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET before serving")
    # This serving profile always requires native Langflow authentication as well.
    os.environ["LANGFLOW_AUTO_LOGIN"] = "false"
    os.environ["LANGFLOW_SKIP_AUTH_AUTO_LOGIN"] = "false"
    from langflow.main import setup_app

    app = setup_app()
    app.add_middleware(
        CallerMiddleware,
        secret=values.get("FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET", ""),
        api_origin=values.get("FLOWBRIDGE_API_ORIGIN", "http://127.0.0.1:8000"),
        flow_id=values.get("FLOWBRIDGE_LANGFLOW_FLOW_ID", "flowbridge-ver2-central"),
    )
    return app
