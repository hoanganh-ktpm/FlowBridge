# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.openapi.utils import get_openapi

from flowbridge.identity.dependencies import get_principal
from flowbridge.identity.schemas import IdentityConfig, Principal
from flowbridge.identity.service import PrincipalContext, identity_config
from flowbridge.shared.correlation import CORRELATION_PARAMETER
from flowbridge.shared.errors import ErrorResponse
from flowbridge.shared.openapi import namespace_schemas, register_fragment

router = APIRouter(
    prefix="/api/v1/core/identity",
    tags=["Identity"],
    responses={
        code: {
            "model": ErrorResponse,
            **(
                {"headers": {"Retry-After": {"schema": {"type": "integer", "minimum": 1}}}}
                if code == 409
                else {}
            ),
        }
        for code in (
            400,
            401,
            403,
            404,
            409,
            422,
            429,
            500,
            503,
            504,
        )
    },
)


@router.get(
    "/config",
    operation_id="getIdentityConfig",
    response_model=IdentityConfig,
    openapi_extra={"parameters": [CORRELATION_PARAMETER]},
)
async def get_config(request: Request) -> IdentityConfig:
    return identity_config(request.app.state.settings)


@router.get(
    "/me",
    operation_id="getPrincipal",
    response_model=Principal,
    openapi_extra={"parameters": [CORRELATION_PARAMETER]},
)
async def get_me(context: Annotated[PrincipalContext, Depends(get_principal)]) -> Principal:
    return context.principal


def install_identity_router(app: FastAPI) -> None:
    app.include_router(router)
    register_fragment(app, "identity", lambda: identity_document(app))


def identity_document(app: FastAPI) -> dict:
    generated = get_openapi(title=app.title, version=app.version, routes=router.routes)
    schema = namespace_schemas(generated, "Core")
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
    }
    for path, entry in schema["paths"].items():
        entry["get"]["security"] = [] if path.endswith("/config") else [{"BearerAuth": []}]
    schema["x-core-identity-version"] = "0.2.0"
    return schema
