# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Router danh tính: config công khai và principal mẫu cố định.

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from flowbridge.identity.schemas import IdentityConfig, Principal
from flowbridge.identity.service import IdentityService
from flowbridge.sample import SampleContext, sample_context

router = APIRouter(prefix="/api/v1/core/identity", tags=["Identity"])


def get_service(request: Request) -> IdentityService:
    # Lấy service danh tính mẫu đã inject ở lifespan.
    return request.app.state.identity_service


@router.get("/config", operation_id="getIdentityConfig", response_model=IdentityConfig)
async def get_identity_config(
    service: Annotated[IdentityService, Depends(get_service)],
) -> IdentityConfig:
    # Trả cấu hình OIDC mẫu, không lộ secret.
    return service.config()


@router.get("/me", operation_id="getPrincipal", response_model=Principal)
async def get_principal(
    service: Annotated[IdentityService, Depends(get_service)],
    context: Annotated[SampleContext, Depends(sample_context)],
) -> Principal:
    # Trả principal cố định từ fixture, bỏ qua header tự khai.
    return service.principal(context)
