# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Opt-in loopback-only HTTP pilot using the existing retrieval DTO."""

from ipaddress import ip_address
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.openapi.utils import get_openapi
from starlette.concurrency import run_in_threadpool

from flowbridge.shared.errors import CoreError, ErrorCode, ErrorResponse
from flowbridge.shared.http import WriteKey

from .development import DEV_NOTICE, DevelopmentKnowledgeService
from .models import RetrievalRequest, RetrievalResponse

PATH = "/internal/v1/tools/retrieve-knowledge"
router = APIRouter(tags=["Supplier Assistant / RAG development"])


def get_service(request: Request) -> DevelopmentKnowledgeService:
    # Do not trust forwarded headers as an application-level permission resolver.
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise CoreError(ErrorCode.FORBIDDEN)
    service = getattr(request.app.state, "rag_dev_service", None)
    if service is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    return service


Service = Annotated[DevelopmentKnowledgeService, Depends(get_service)]


@router.post(
    PATH,
    operation_id="retrieveSupplierKnowledge",
    response_model=RetrievalResponse,
    summary="Development: retrieve evidence from local supplier Markdown",
    description=(
        DEV_NOTICE + ". Enable FLOWBRIDGE_RAG_DEV_ENABLED and call from loopback. "
        "Idempotency-Key is shape-validated only; no durable replay is implemented. "
        "The model is reused across requests and never downloaded by this endpoint."
    ),
    responses={code: {"model": ErrorResponse} for code in (400, 403, 422, 429, 500, 503)},
    openapi_extra={
        "security": [],
        "x-development-only": True,
        "x-production-required-role": "AI_SERVICE",
        "x-production-security": [
            {"ServiceAuth": [], "UserContext": []},
            {"ServiceAuth": [], "JobContext": []},
        ],
    },
)
async def retrieve_knowledge(
    body: RetrievalRequest, service: Service, response: Response, _key: WriteKey
) -> RetrievalResponse:
    result = await run_in_threadpool(service.search, body)
    response.headers["X-FlowBridge-RAG-Mode"] = "development-local-fixtures"
    return result


def install_development_router(app: FastAPI, *, sample_mode: bool) -> None:
    app.include_router(router)
    original_openapi = app.openapi

    def development_openapi() -> dict:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = original_openapi()
        if sample_mode:
            # Core sample OpenAPI comes from a fixed contract, not app.routes.
            # Merge only this real route, with namespaced DTOs to preserve Core schemas.
            extra = get_openapi(title="RAG development", version="0.1.0", routes=router.routes)
            names = set(extra.get("components", {}).get("schemas", {}))

            def namespace(value):
                if isinstance(value, dict):
                    return {key: namespace(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [namespace(item) for item in value]
                if isinstance(value, str) and value.startswith("#/components/schemas/"):
                    name = value.rsplit("/", 1)[-1]
                    return f"#/components/schemas/RagDev{name}" if name in names else value
                return value

            schema["paths"][PATH] = namespace(extra["paths"][PATH])
            target = schema.setdefault("components", {}).setdefault("schemas", {})
            for name, definition in extra.get("components", {}).get("schemas", {}).items():
                target[f"RagDev{name}"] = namespace(definition)
        schema["x-rag-development-mode"] = True
        app.openapi_schema = schema
        return schema

    app.openapi = development_openapi
