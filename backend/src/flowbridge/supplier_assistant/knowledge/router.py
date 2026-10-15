# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
# Opt-in loopback-only HTTP pilot using the existing retrieval DTO.

from ipaddress import ip_address
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, Request, Response
from starlette.concurrency import run_in_threadpool

from flowbridge.shared.errors import CoreError, ErrorCode, ErrorResponse

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
WriteKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


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


def install_development_router(app: FastAPI) -> None:
    app.include_router(router)
    original_openapi = app.openapi

    def development_openapi() -> dict:
        schema = original_openapi()
        schema["x-rag-development-mode"] = True
        return schema

    app.openapi = development_openapi
