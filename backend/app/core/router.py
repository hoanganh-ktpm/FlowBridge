from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from starlette.responses import JSONResponse

from app.core.applications.runtime import ImportRequest, OnboardingSubmission, PlanRequest
from app.core.applications.schemas import ApplicationCreate, ApplicationPatch
from app.core.data_products.querying import QueryScan, QuerySelection
from app.core.data_products.relationships import RelationshipDefinition, RelationshipPatch
from app.core.data_products.schemas import (
    ContractCreate,
    ContractPatch,
    ProductCreate,
    PublishProduct,
)
from app.core.data_sources.schemas import SourceCreate
from app.core.governance.schemas import (
    AccessRequestCreate,
    ApproveRequest,
    ReasonCommand,
    RevisionCommand,
    RevokeGrant,
)
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity, authenticated_identity

router = APIRouter(prefix="/api/v1/core")
Actor = Annotated[VerifiedIdentity, Depends(authenticated_identity)]


def service(request: Request):
    return request.app.state.core


def idempotency_key(
    request: Request,
    key: Annotated[
        str,
        Header(
            alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
        ),
    ],
) -> str:
    if len(request.headers.getlist("Idempotency-Key")) != 1:
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    return key


Key = Annotated[str, Depends(idempotency_key)]


@router.get("/me", operation_id="getMyContext")
async def get_my_context(request: Request, actor: Actor):
    from app.core.governance.policy import resolve_principal

    async with service(request).store.transaction() as transaction:
        principal = await resolve_principal(transaction, actor)
    return {
        "verified_identity": {
            "issuer": actor.issuer,
            "subject": actor.subject,
            "client_id": actor.client_id,
            "account_type": actor.account_type,
            "scopes": sorted(actor.scopes),
        },
        **principal.model_dump(mode="json"),
    }


@router.post("/application-onboarding/validate", operation_id="validateApplicationOnboarding")
async def validate_onboarding(body: OnboardingSubmission, request: Request, actor: Actor):
    return await request.app.state.onboarding.validate(actor, body)


@router.post("/application-onboarding/plans", operation_id="planApplicationOnboarding")
async def plan_onboarding(body: PlanRequest, request: Request, actor: Actor):
    return await request.app.state.onboarding.plan(actor, body)


@router.post(
    "/application-onboarding/imports", operation_id="importApplicationDrafts", status_code=201
)
async def import_onboarding(body: ImportRequest, request: Request, actor: Actor, key: Key):
    receipt = await request.app.state.onboarding.import_drafts(
        actor, body, request.method, request.url.path, key, request.state.correlation_id
    )
    return JSONResponse(receipt.body, receipt.status)


async def command(request, actor, key, operation, body, **ids):
    canonical_path = request.scope["route"].path
    for name, value in request.path_params.items():
        canonical_path = canonical_path.replace("{" + name + "}", str(UUID(value)))
    receipt = await service(request).command(
        operation,
        actor,
        request.method,
        canonical_path,
        key,
        body.model_dump(mode="json", by_alias=True, exclude_unset=True),
        request.state.correlation_id,
        **ids,
    )
    headers = {"Location": receipt.location} if receipt.location else {}
    if receipt.status == 202:
        headers["Retry-After"] = "1"
    return JSONResponse(receipt.body, receipt.status, headers=headers)


@router.post("/applications", operation_id="createApplication", status_code=201)
async def create_application(body: ApplicationCreate, request: Request, actor: Actor, key: Key):
    return await command(request, actor, key, "create_application", body)


@router.patch("/applications/{application_id}", operation_id="patchApplication")
async def patch_application(
    application_id: UUID, body: ApplicationPatch, request: Request, actor: Actor, key: Key
):
    return await command(
        request, actor, key, "patch_application", body, application_id=application_id
    )


@router.post("/data-sources", operation_id="createSource", status_code=201)
async def create_source(body: SourceCreate, request: Request, actor: Actor, key: Key):
    return await command(request, actor, key, "create_source", body)


@router.get("/data-sources/{data_source_id}", operation_id="getSource")
async def get_source(data_source_id: UUID, request: Request, actor: Actor):
    return await service(request).get(actor, "source", data_source_id)


@router.post(
    "/data-sources/{data_source_id}/connect", operation_id="connectSource", status_code=202
)
async def connect_source(
    data_source_id: UUID, body: RevisionCommand, request: Request, actor: Actor, key: Key
):
    return await command(request, actor, key, "connect_source", body, data_source_id=data_source_id)


def identifiers(value: str | None) -> tuple[str, ...]:
    import re

    if value is None:
        return ()
    items = tuple(value.split(","))
    if (
        not 1 <= len(items) <= 100
        or len(set(items)) != len(items)
        or any(not re.fullmatch(r"(?!__)[a-z_][a-z0-9_]{0,62}", item) for item in items)
    ):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    return items


@router.get("/data-sources/{data_source_id}/schema", operation_id="discoverSourceSchema")
async def discover_schema(
    data_source_id: UUID,
    request: Request,
    actor: Actor,
    schema_name: Annotated[str | None, Query(pattern=r"^[a-z_][a-z0-9_]{0,62}$")] = None,
    object_names: str | None = None,
):
    for name in ("schema_name", "object_names"):
        if len(request.query_params.getlist(name)) > 1:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
    if schema_name is not None:
        identifiers(schema_name)
    return await service(request).discover(
        actor, data_source_id, schema_name, identifiers(object_names)
    )


@router.post("/data-products", operation_id="createProduct", status_code=201)
async def create_product(body: ProductCreate, request: Request, actor: Actor, key: Key):
    return await command(request, actor, key, "create_product", body)


@router.get("/authorized-data-products", operation_id="listAuthorizedDataProducts")
async def list_authorized_products(request: Request, actor: Actor):
    return await service(request).authorized_products(actor)


@router.get("/data-products/{data_product_id}", operation_id="getProduct")
async def get_product(data_product_id: UUID, request: Request, actor: Actor):
    return await service(request).get(actor, "product", data_product_id)


@router.post(
    "/data-products/{data_product_id}/contracts", operation_id="createDataContract", status_code=201
)
async def create_contract(
    data_product_id: UUID, body: ContractCreate, request: Request, actor: Actor, key: Key
):
    return await command(
        request, actor, key, "create_contract", body, data_product_id=data_product_id
    )


@router.get("/data-contracts/{contract_version_id}", operation_id="getDataContract")
async def get_contract(contract_version_id: UUID, request: Request, actor: Actor):
    return await service(request).get(actor, "contract", contract_version_id)


@router.patch("/data-contracts/{contract_version_id}", operation_id="patchDataContract")
async def patch_contract(
    contract_version_id: UUID, body: ContractPatch, request: Request, actor: Actor, key: Key
):
    return await command(
        request, actor, key, "patch_contract", body, contract_version_id=contract_version_id
    )


@router.post("/data-contracts/{contract_version_id}/publish", operation_id="publishDataContract")
async def publish_contract(
    contract_version_id: UUID, body: RevisionCommand, request: Request, actor: Actor, key: Key
):
    return await command(
        request, actor, key, "publish_contract", body, contract_version_id=contract_version_id
    )


@router.post(
    "/data-products/{data_product_id}/publish", operation_id="publishDataProduct", status_code=202
)
async def publish_product(
    data_product_id: UUID, body: PublishProduct, request: Request, actor: Actor, key: Key
):
    return await command(
        request, actor, key, "publish_product", body, data_product_id=data_product_id
    )


@router.post(
    "/data-products/{data_product_id}/disable", operation_id="disableDataProduct", status_code=202
)
async def disable_product(
    data_product_id: UUID, body: ReasonCommand, request: Request, actor: Actor, key: Key
):
    return await command(
        request, actor, key, "disable_product", body, data_product_id=data_product_id
    )


@router.post("/access-requests", operation_id="createAccessRequest", status_code=201)
async def create_request(body: AccessRequestCreate, request: Request, actor: Actor, key: Key):
    return await command(request, actor, key, "create_request", body)


@router.get("/access-requests/{access_request_id}", operation_id="getAccessRequest")
async def get_request(access_request_id: UUID, request: Request, actor: Actor):
    return await service(request).get(actor, "request", access_request_id)


@router.post(
    "/access-requests/{access_request_id}/approve",
    operation_id="approveAccessRequest",
    status_code=202,
)
async def approve_request(
    access_request_id: UUID, body: ApproveRequest, request: Request, actor: Actor, key: Key
):
    return await command(
        request, actor, key, "approve_request", body, access_request_id=access_request_id
    )


@router.get("/access-grants/{access_grant_id}", operation_id="getGrant")
async def get_grant(access_grant_id: UUID, request: Request, actor: Actor):
    return await service(request).get(actor, "grant", access_grant_id)


@router.post("/access-grants/{access_grant_id}/revoke", operation_id="revokeGrant", status_code=202)
async def revoke_grant(
    access_grant_id: UUID, body: RevokeGrant, request: Request, actor: Actor, key: Key
):
    return await command(request, actor, key, "revoke_grant", body, access_grant_id=access_grant_id)


@router.get("/publication-jobs/{job_id}", operation_id="getPublicationJob")
async def get_job(job_id: UUID, request: Request, actor: Actor):
    return await service(request).get(actor, "job", job_id)


@router.get("/data-products/{data_product_id}/rows", operation_id="readDataProductRows")
async def read_rows(
    data_product_id: UUID,
    contract_version_id: UUID,
    columns: str,
    request: Request,
    actor: Actor,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    for name in ("contract_version_id", "columns", "limit", "offset"):
        if len(request.query_params.getlist(name)) > 1:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
    return await service(request).read_rows(
        actor, data_product_id, contract_version_id, identifiers(columns), limit, offset
    )


@router.post("/relationships", operation_id="createRelationship", status_code=201)
async def create_relationship(
    body: RelationshipDefinition, request: Request, actor: Actor, key: Key
):
    receipt = await request.app.state.relationships.command(
        "create",
        actor,
        "POST",
        request.url.path,
        key,
        body.model_dump(mode="json"),
        request.state.correlation_id,
    )
    return JSONResponse(receipt.body, status_code=receipt.status)


@router.get("/relationships", operation_id="listRelationships")
async def list_relationships(source_product_id: UUID, request: Request, actor: Actor):
    return await request.app.state.relationships.list(actor, source_product_id)


@router.get("/relationships/{relationship_id}", operation_id="getRelationship")
async def get_relationship(relationship_id: UUID, request: Request, actor: Actor):
    return await request.app.state.relationships.get(actor, relationship_id)


@router.patch("/relationships/{relationship_id}", operation_id="patchRelationship")
async def patch_relationship(
    relationship_id: UUID,
    body: RelationshipPatch,
    request: Request,
    actor: Actor,
    key: Key,
):
    receipt = await request.app.state.relationships.command(
        "patch",
        actor,
        "PATCH",
        request.url.path,
        key,
        body.model_dump(mode="json"),
        request.state.correlation_id,
        relationship_id=relationship_id,
    )
    return JSONResponse(receipt.body, status_code=receipt.status)


@router.post(
    "/relationships/{relationship_id}/publish", operation_id="publishRelationship", status_code=202
)
async def publish_relationship(
    relationship_id: UUID,
    body: RevisionCommand,
    request: Request,
    actor: Actor,
    key: Key,
):
    receipt = await request.app.state.relationships.command(
        "publish",
        actor,
        "POST",
        request.url.path,
        key,
        body.model_dump(mode="json"),
        request.state.correlation_id,
        relationship_id=relationship_id,
    )
    return JSONResponse(
        receipt.body,
        status_code=receipt.status,
        headers={"Location": receipt.location, "Retry-After": "1"},
    )


@router.post(
    "/relationships/{relationship_id}/disable", operation_id="disableRelationship", status_code=202
)
async def disable_relationship(
    relationship_id: UUID,
    body: ReasonCommand,
    request: Request,
    actor: Actor,
    key: Key,
):
    receipt = await request.app.state.relationships.command(
        "disable",
        actor,
        "POST",
        request.url.path,
        key,
        body.model_dump(mode="json"),
        request.state.correlation_id,
        relationship_id=relationship_id,
    )
    return JSONResponse(
        receipt.body,
        status_code=receipt.status,
        headers={"Location": receipt.location, "Retry-After": "1"},
    )


@router.post("/data-products/{data_product_id}/query", operation_id="queryDataProduct")
async def query_product(
    data_product_id: UUID,
    body: QuerySelection,
    request: Request,
    actor: Actor,
):
    return await request.app.state.relationship_queries.run(
        actor, data_product_id, body, request.state.correlation_id
    )


@router.post("/data-products/{data_product_id}/scan", operation_id="scanDataProduct")
async def scan_product(data_product_id: UUID, body: QueryScan, request: Request, actor: Actor):
    return await request.app.state.relationship_queries.run(
        actor, data_product_id, body.selection, request.state.correlation_id, pages=body.pages
    )
