# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import asyncio
import json
from time import monotonic
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from jsonschema.exceptions import ValidationError
from starlette.responses import JSONResponse, StreamingResponse

from flowbridge.agent_hub.contract import (
    HubError,
    check,
    document,
    encoded,
    openapi_fragment,
    validate,
)
from flowbridge.agent_hub.projection import TERMINAL
from flowbridge.agent_hub.service import HubService
from flowbridge.identity.authentication import (
    VerifiedIdentity,
    authenticated_identity,
    verify_access_token,
)
from flowbridge.shared.errors import ERROR_SPECS, CoreError
from flowbridge.shared.openapi import register_fragment


class HubRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            trace = uuid4().hex
            headers = {"Cache-Control": "no-store", "X-Request-ID": trace}
            try:
                response = await original(request)
                for key, value in headers.items():
                    if key not in response.headers:
                        response.headers[key] = value
                return response
            except CoreError as exc:
                spec = ERROR_SPECS[exc.code]
                error = HubError(spec.status_code, exc.code.value, spec.message, spec.retryable)
            except HubError as exc:
                error = exc
            except (ValidationError, RequestValidationError, ValueError, TypeError):
                error = HubError(422, "VALIDATION_ERROR", "Dữ liệu yêu cầu không hợp lệ.")
            except Exception:
                error = HubError(500, "INTERNAL_ERROR", "Không xử lý được yêu cầu.")
            if error.status == 401:
                headers["WWW-Authenticate"] = "Bearer"
            if error.status in {429, 503} or error.code in {
                "CONVERSATION_BUSY",
                "IDEMPOTENCY_IN_PROGRESS",
            }:
                headers["Retry-After"] = "2"
            return JSONResponse(
                {
                    "error": {
                        "code": error.code,
                        "message": error.message,
                        "retryable": error.retryable,
                        "trace_id": trace,
                    }
                },
                status_code=error.status,
                headers=headers,
            )

        return handle


async def body_json(request: Request) -> dict:
    check(
        request.headers.get("content-type", "").split(";")[0].strip() == "application/json",
        415,
        "UNSUPPORTED_MEDIA_TYPE",
        "Cần Content-Type application/json.",
    )
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        check(len(raw) <= 131072, 413, "PAYLOAD_TOO_LARGE", "Request vượt giới hạn 128 KiB.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=pairs)
    check(isinstance(value, dict), 422, "VALIDATION_ERROR", "Body phải là JSON object.")
    return value


def idempotency_key(request: Request) -> str:
    values = request.headers.getlist("idempotency-key")
    check(
        len(values) == 1
        and 16 <= len(values[0]) <= 128
        and values[0].isascii()
        and all(33 <= ord(c) <= 126 for c in values[0]),
        422,
        "INVALID_IDEMPOTENCY_KEY",
        "Cần Idempotency-Key gồm 16–128 ký tự ASCII hiển thị.",
    )
    return values[0]


async def events_response(
    service: HubService, identity: VerifiedIdentity, rid: str, request: Request
) -> StreamingResponse:
    cursors = request.headers.getlist("last-event-id")
    check(len(cursors) <= 1, 400, "INVALID_EVENT_CURSOR", "Last-Event-ID không hợp lệ.")
    last_id = cursors[0] if cursors else None
    initial = await service.read_events(identity, rid, last_id)

    async def generate():
        nonlocal last_id, identity
        pending = initial
        heartbeat = monotonic()
        try:
            while True:
                items, run = pending
                for event in items:
                    last_id = event["id"]
                    yield (
                        f"id: {event['id']}\nevent: {event['type']}\ndata: ".encode()
                        + encoded(event)
                        + b"\n\n"
                    )
                if run["status"] in TERMINAL or await request.is_disconnected():
                    return
                if monotonic() - heartbeat >= 15:
                    identity = await verify_access_token(
                        request, request.headers["authorization"].split(" ", 1)[1]
                    )
                    yield b": heartbeat\n\n"
                    heartbeat = monotonic()
                await asyncio.sleep(1)
                pending = await service.read_events(identity, rid, last_id)
        except Exception:
            # Headers have already been sent; client reconnects or polls result/metadata.
            return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def endpoint(operation: str):
    async def handle(
        request: Request, identity: Annotated[VerifiedIdentity, Depends(authenticated_identity)]
    ):
        resources = request.app.state.resources
        check(
            resources and resources.sessions, 503, "SERVICE_UNAVAILABLE", "Backend chưa sẵn sàng."
        )
        service = HubService(
            resources.sessions,
            resources.http,
            request.app.state.settings,
            request.app.state.rag_postgres_service,
        )
        cid, rid = request.path_params.get("conversation_id"), request.path_params.get("run_id")
        for value in (cid, rid):
            if value is not None:
                validate(value, "HubId")
        if operation == "listAgentHubAgents":
            async with service.sessions() as session, session.begin():
                await service.authorize(session, identity)
            return JSONResponse(service.catalog())
        if operation == "searchAgentHubKnowledge":
            return JSONResponse(await service.search_knowledge(identity, await body_json(request)))
        if operation in {"listAgentHubConversations", "listAgentHubMessages", "listAgentHubRuns"}:
            check(
                not set(request.query_params) - {"cursor", "limit"},
                422,
                "VALIDATION_ERROR",
                "Query parameter không hỗ trợ.",
            )
            check(
                all(len(request.query_params.getlist(k)) == 1 for k in request.query_params),
                422,
                "VALIDATION_ERROR",
                "Query parameter bị lặp.",
            )
            limit, cursor = (
                int(request.query_params.get("limit", "20")),
                request.query_params.get("cursor"),
            )
            check(
                1 <= limit <= 100 and (cursor is None or 1 <= len(cursor) <= 2048),
                422,
                "VALIDATION_ERROR",
                "Phân trang không hợp lệ.",
            )
            kind = {
                "listAgentHubConversations": "conversations",
                "listAgentHubMessages": "messages",
                "listAgentHubRuns": "runs",
            }[operation]
            return JSONResponse(await service.page(identity, kind, cid, cursor, limit))
        if operation == "getAgentHubConversation":
            result = await service.get_conversation(identity, cid)
            return JSONResponse(result, headers={"ETag": f'"{result["revision"]}"'})
        if operation == "updateAgentHubConversation":
            matches = request.headers.getlist("if-match")
            check(len(matches) <= 1, 412, "PRECONDITION_FAILED", "If-Match không hợp lệ.")
            result = await service.update_conversation(
                identity, cid, await body_json(request), matches[0] if matches else None
            )
            return JSONResponse(result, headers={"ETag": f'"{result["revision"]}"'})
        if operation in {"getAgentHubRunResult", "getAgentHubRunMetadata"}:
            run = await service.get_run(identity, rid)
            return JSONResponse(
                run["result" if operation == "getAgentHubRunResult" else "metadata"]
            )
        if operation == "streamAgentHubRunEvents":
            return await events_response(service, identity, rid, request)
        key = idempotency_key(request)
        if operation == "createAgentHubConversation":
            response = await service.create_conversation(identity, await body_json(request), key)
        elif operation == "createAgentHubRun":
            response = await service.create_run(
                identity,
                cid,
                await body_json(request),
                key,
                request.headers["authorization"].split(" ", 1)[1],
            )
        elif operation == "cancelAgentHubRun":
            async for chunk in request.stream():
                check(not chunk, 422, "VALIDATION_ERROR", "Cancel không nhận request body.")
            response = await service.cancel(identity, rid, key)
        else:
            raise RuntimeError("Unsupported Hub operation")
        return JSONResponse(response["body"], response["status"], headers=response["headers"])

    return handle


def install_hub(app: FastAPI) -> None:
    router = APIRouter(route_class=HubRoute)
    for path, entry in document()["paths"].items():
        for method, operation in entry.items():
            if method not in {"get", "post", "patch"}:
                continue
            router.add_api_route(
                path,
                endpoint(operation["operationId"]),
                methods=[method.upper()],
                operation_id=operation["operationId"],
                include_in_schema=False,
            )
    app.include_router(router)
    register_fragment(app, "agent-hub", openapi_fragment)
