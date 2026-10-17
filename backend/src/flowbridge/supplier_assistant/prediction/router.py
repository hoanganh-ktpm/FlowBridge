# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.openapi.utils import get_openapi
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flowbridge.db import get_session
from flowbridge.identity.authentication import VerifiedIdentity, authenticated_identity
from flowbridge.shared.errors import CoreError, ErrorCode, ErrorResponse
from flowbridge.shared.openapi import namespace_schemas, register_fragment
from flowbridge.supplier_assistant.repository import SupplierRepository

from .repository import PredictionRepository
from .schemas import OrderRisk, RankRequest, RankResponse, ScenarioRequest, ScenarioResponse
from .service import PredictionService

router = APIRouter(
    tags=["Supplier Assistant / ML"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503, 504)},
)


async def get_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[VerifiedIdentity, Depends(authenticated_identity)],
) -> AsyncIterator[PredictionService]:
    settings = request.app.state.settings
    runtime = request.app.state.ml_runtime
    if runtime is None:
        raise CoreError(ErrorCode.MODEL_UNAVAILABLE)
    try:
        async with asyncio.timeout(settings.ml_request_timeout_seconds), session.begin():
            # Stable read snapshots for lookup/ranking. Writes use row/advisory locks instead.
            if not request.url.path.startswith("/internal/"):
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            service = PredictionService(
                PredictionRepository(
                    SupplierRepository(session), max_rows=settings.ml_max_history_rows
                ),
                runtime,
                settings,
            )
            await service.authorize(identity, write=request.url.path.startswith("/internal/"))
            yield service
    except IntegrityError:
        raise CoreError(ErrorCode.IDEMPOTENCY_CONFLICT) from None
    except TimeoutError:
        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
    except DBAPIError:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None


Service = Annotated[PredictionService, Depends(get_service, scope="function")]
WriteKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    ),
]


@router.post(
    "/api/v1/ml/scenarios/predict",
    response_model=ScenarioResponse,
    operation_id="predictMLScenario",
    summary="Score one supplier/product purchase scenario",
)
async def predict_scenario(body: ScenarioRequest, service: Service) -> ScenarioResponse:
    return await service.scenario(body)


@router.post(
    "/api/v1/ml/suppliers/rank",
    response_model=RankResponse,
    operation_id="rankMLSuppliers",
    summary="Rank eligible suppliers by delivery risk",
)
async def rank_suppliers(body: RankRequest, service: Service) -> RankResponse:
    return await service.rank(body)


@router.post(
    "/internal/v1/ml/purchase-orders/{order_id}/predict",
    response_model=OrderRisk,
    operation_id="predictMLPurchaseOrder",
    summary="AI_SERVICE: score open PO items and persist immutable predictions",
)
async def predict_order(order_id: UUID, service: Service, key: WriteKey) -> OrderRisk:
    return await service.predict_order(order_id, key)


@router.get(
    "/api/v1/ml/purchase-orders/{order_id}/risk",
    response_model=OrderRisk,
    operation_id="getMLPurchaseOrderRisk",
    summary="Read fresh risk: maximum over open items",
)
async def order_risk(order_id: UUID, service: Service) -> OrderRisk:
    return await service.current_order_risk(order_id)


@router.get(
    "/api/v1/ml/predictions/{prediction_id}",
    operation_id="getMLPrediction",
    summary="Read a persisted prediction after resource authorization",
)
async def prediction(prediction_id: UUID, service: Service) -> dict:
    return await service.prediction(prediction_id)


async def model_readiness(request: Request) -> dict:
    runtime = request.app.state.ml_runtime
    if runtime is None:
        raise CoreError(ErrorCode.MODEL_UNAVAILABLE)
    runtime.ensure_ready()
    return {"status": "ready", "scope": "ml_model", "score_is_calibrated": False}


def install_prediction_router(app: FastAPI) -> None:
    app.include_router(router)
    app.add_api_route("/_ops/ml/ready", model_readiness, include_in_schema=False)
    register_fragment(app, "prediction", lambda: prediction_document(app))


def prediction_document(app: FastAPI) -> dict:
    generated = get_openapi(title=app.title, version=app.version, routes=router.routes)
    schema = namespace_schemas(generated, "ML")
    schema["x-ml-api-version"] = "1.0.0"
    return schema
