# SPDX-License-Identifier: GPL-3.0-or-later
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.openapi.utils import get_openapi
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError

from flowbridge.identity.authentication import VerifiedIdentity, authenticated_identity
from flowbridge.shared.errors import CoreError, ErrorCode, ErrorResponse
from flowbridge.shared.openapi import namespace_schemas, register_fragment

from .manager_models import Create, Edit, Revision

router = APIRouter(
    prefix="/api/admin/knowledge",
    tags=["Knowledge / Admin"],
    responses={n: {"model": ErrorResponse} for n in (401, 403, 404, 409, 422, 503)},
)
Identity = Annotated[VerifiedIdentity, Depends(authenticated_identity)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=160)]


def input_schema(model):
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


def manager(request):
    service = getattr(request.app.state, "knowledge_manager", None)
    if service is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    return service


async def body(request, model):
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    # Bound raw JSON before decoding; escaping can expand UTF-8 content by up to 6x.
    data = bytearray()
    maximum = request.app.state.settings.rag_manager_max_bytes
    async for part in request.stream():
        data.extend(part)
        if len(data) > maximum * 6 + 8192:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
    try:
        return model.model_validate(json.loads(data)).model_dump(mode="json")
    except (ValidationError, ValueError, UnicodeError):
        raise CoreError(ErrorCode.VALIDATION_ERROR) from None


async def call(awaitable):
    try:
        return await awaitable
    except IntegrityError:
        raise CoreError(ErrorCode.VERSION_CONFLICT) from None
    except DBAPIError:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None


@router.get("/options", operation_id="knowledgeManagerOptions")
async def options(request: Request, identity: Identity):
    return await call(manager(request).options(identity))


@router.get("/documents", operation_id="listManagedKnowledge")
async def documents(request: Request, identity: Identity):
    return await call(manager(request).listing(identity))


@router.get("/documents/{identifier}", operation_id="readManagedKnowledge")
async def read(identifier: UUID, request: Request, identity: Identity):
    return await call(manager(request).detail(identity, identifier))


@router.get("/documents/{identifier}/versions/{revision}", operation_id="readKnowledgeVersion")
async def history(identifier: UUID, revision: int, request: Request, identity: Identity):
    return await call(manager(request).history(identity, identifier, revision))


@router.post(
    "/documents",
    status_code=202,
    operation_id="createManagedKnowledge",
    openapi_extra=input_schema(Create),
)
async def create(request: Request, identity: Identity, key: Key):
    payload = await body(request, Create)
    return await call(manager(request).enqueue(identity, key, "publish", payload))


@router.put(
    "/documents/{identifier}",
    status_code=202,
    operation_id="editManagedKnowledge",
    openapi_extra=input_schema(Edit),
)
async def edit(identifier: UUID, request: Request, identity: Identity, key: Key):
    payload = await body(request, Edit)
    return await call(manager(request).enqueue(identity, key, "publish", payload, identifier))


@router.post(
    "/documents/{identifier}/reindex",
    status_code=202,
    operation_id="reindexManagedKnowledge",
    openapi_extra=input_schema(Revision),
)
async def reindex(identifier: UUID, request: Request, identity: Identity, key: Key):
    payload = await body(request, Revision)
    return await call(manager(request).enqueue(identity, key, "reindex", payload, identifier))


@router.post(
    "/documents/{identifier}/withdraw",
    status_code=202,
    operation_id="withdrawManagedKnowledge",
    openapi_extra=input_schema(Revision),
)
async def withdraw(identifier: UUID, request: Request, identity: Identity, key: Key):
    payload = await body(request, Revision)
    return await call(manager(request).enqueue(identity, key, "withdraw", payload, identifier))


@router.delete(
    "/documents/{identifier}",
    status_code=202,
    operation_id="deleteManagedKnowledge",
    openapi_extra=input_schema(Revision),
)
async def delete(identifier: UUID, request: Request, identity: Identity, key: Key):
    payload = await body(request, Revision)
    return await call(manager(request).enqueue(identity, key, "delete", payload, identifier))


@router.get("/jobs/{identifier}", operation_id="readKnowledgeJob")
async def job(identifier: UUID, request: Request, identity: Identity):
    return await call(manager(request).job(identity, identifier))


@router.post("/jobs/{identifier}/retry", status_code=202, operation_id="retryKnowledgeJob")
async def retry(identifier: UUID, request: Request, identity: Identity, key: Key):
    return await call(manager(request).retry(identity, key, identifier))


def install_manager_router(app: FastAPI):
    app.include_router(router)
    register_fragment(
        app,
        "knowledge_manager",
        lambda: namespace_schemas(
            get_openapi(title=app.title, version=app.version, routes=router.routes), "KM"
        ),
    )
