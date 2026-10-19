# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.openapi.utils import get_openapi
from sqlalchemy.exc import DBAPIError

from flowbridge.identity.authentication import VerifiedIdentity, authenticated_identity
from flowbridge.shared.errors import CoreError, ErrorCode, ErrorResponse
from flowbridge.shared.openapi import namespace_schemas, register_fragment

from .delivery_statistics import (
    DeliveryOrdersRequest,
    DeliveryStatistics,
    StatisticsRequest,
    calculate_statistics,
)

router = APIRouter(
    tags=["Supplier Assistant / Delivery statistics"],
    responses={n: {"model": ErrorResponse} for n in (401, 403, 404, 409, 422, 503, 504)},
)


@router.post(
    "/internal/v1/tools/supplier-delivery-statistics",
    response_model=DeliveryStatistics,
    operation_id="getSupplierDeliveryStatistics",
)
async def statistics(
    body: StatisticsRequest,
    request: Request,
    identity: Annotated[VerifiedIdentity, Depends(authenticated_identity)],
    task_id: Annotated[UUID, Header(alias="X-Job-Context")],
):
    if (
        request.headers.getlist("X-User-Context")
        or len(request.headers.getlist("X-Job-Context")) != 1
    ):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    resources = request.app.state.resources
    if resources is None or resources.sessions is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    try:
        async with asyncio.timeout(30):
            return await calculate_statistics(
                resources.sessions, identity, task_id, body.supplier_id, body
            )
    except DBAPIError:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
    except TimeoutError:
        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None


@router.post(
    "/internal/v1/tools/supplier-delivery-orders",
    response_model=DeliveryStatistics,
    operation_id="getVisibleSupplierDeliveryOrders",
)
async def delivery_orders(
    body: DeliveryOrdersRequest,
    request: Request,
    identity: Annotated[VerifiedIdentity, Depends(authenticated_identity)],
):
    if request.headers.getlist("X-User-Context") or request.headers.getlist("X-Job-Context"):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    resources = request.app.state.resources
    if resources is None or resources.sessions is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    try:
        async with asyncio.timeout(30):
            return await calculate_statistics(
                resources.sessions, identity, None, body.supplier_id, body
            )
    except DBAPIError:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
    except TimeoutError:
        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None


def install_delivery_statistics_router(app: FastAPI):
    app.include_router(router)
    register_fragment(
        app,
        "delivery_statistics",
        lambda: namespace_schemas(
            get_openapi(title=app.title, version=app.version, routes=router.routes),
            "DS",
        ),
    )
