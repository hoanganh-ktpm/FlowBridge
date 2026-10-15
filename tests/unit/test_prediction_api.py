# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from flowbridge.db import get_session
from flowbridge.gateway import route_map
from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.prediction.client import MLClientError, SupplierMLClient
from flowbridge.supplier_assistant.prediction.model import TabICLRuntime
from flowbridge.supplier_assistant.prediction.router import get_service
from flowbridge.supplier_assistant.prediction.schemas import (
    ItemRisk,
    RankRequest,
    RiskResult,
    ScenarioRequest,
)
from flowbridge.supplier_assistant.prediction.service import PredictionService, aggregate_order

DSN = "postgresql+asyncpg://test:password@localhost/test"


def settings(**kwargs):
    return Settings(_env_file=None, database_url=DSN, ml_enabled=True, **kwargs)


def test_order_max_tracks_item_deadline_and_does_not_hide_missing_scores():
    now = datetime.now(UTC)
    a, b, missing = uuid4(), uuid4(), uuid4()
    items = [
        ItemRisk(
            po_item_id=item,
            product_id=uuid4(),
            requested_delivery_date=(now + timedelta(days=days)).date(),
            result=RiskResult(status="success", risk_score=score, risk_threshold=0.7),
        )
        for item, score, days in [(a, 0.8, 10), (b, 0.3, 3)]
    ]
    result = aggregate_order(uuid4(), items)
    assert result.representative_risk_score == 0.8
    assert result.highest_risk_item_id == a
    assert result.highest_risk_item_deadline == (now + timedelta(days=10)).date()
    assert result.is_complete
    items.append(
        ItemRisk(
            po_item_id=missing,
            product_id=uuid4(),
            requested_delivery_date=now.date(),
            result=RiskResult(status="insufficient_data", risk_threshold=0.7),
        )
    )
    result = aggregate_order(uuid4(), items)
    assert result.status == "partial"
    assert result.representative_risk_score == 0.8
    assert not result.is_complete
    assert aggregate_order(uuid4(), []).status == "no_open_items"


def test_ml_openapi_preserves_legacy_security_and_includes_new_routes():
    app = create_app(settings(docs_enabled=True))
    schema = app.openapi()
    assert len(schema["paths"]) >= 5
    assert schema["x-ml-api-version"] == "1.0.0"
    path = schema["paths"]["/api/v1/ml/suppliers/rank"]["post"]
    assert path["security"] == [{"HTTPBearer": []}]
    assert path["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/MLRankRequest"
    )
    assert schema["security"]
    assert app.openapi() == schema
    routes = route_map(include_ml=True)
    assert len(routes) == len(route_map()) + 5
    assert (
        next(r for r in routes if r.operation_id == "predictMLPurchaseOrder").visibility
        == "internal"
    )


def test_ml_configuration_requires_database_and_valid_threshold():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ml_enabled=True, database_url=None)
    with pytest.raises(ValidationError):
        settings(ml_risk_threshold=float("nan"))


@pytest.mark.asyncio
async def test_ml_http_requires_bearer_and_does_not_trust_actor_headers():
    app = create_app(settings())
    app.dependency_overrides[get_session] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        result = await c.post(
            "/api/v1/ml/suppliers/rank",
            json={},
            headers={"X-User-Id": "admin", "X-Role": "AI_SERVICE"},
        )
    assert result.status_code == 401


@pytest.mark.asyncio
async def test_ml_http_rejects_forged_features_before_service_calls():
    app = create_app(settings())
    service = SimpleNamespace(rank=AsyncMock())
    app.dependency_overrides[get_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        result = await c.post(
            "/api/v1/ml/suppliers/rank",
            json={
                "product_id": str(uuid4()),
                "quantity": "100",
                "requested_delivery_date": "2026-12-01",
                "feature_asof": "2020-01-01T00:00:00Z",
            },
        )
    assert result.status_code == 422
    service.rank.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_unavailable_readiness_does_not_fabricate_scores():
    app = create_app(settings())
    app.state.ml_runtime = TabICLRuntime(settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        result = await c.get("/_ops/ml/ready")
    assert result.status_code == 503
    assert result.json()["code"] == "MODEL_UNAVAILABLE"
    assert "risk_score" not in result.json()


@pytest.mark.asyncio
async def test_rank_filters_candidates_then_scores_them_in_one_batch():
    product_id = uuid4()
    a, b, blocked = uuid4(), uuid4(), uuid4()
    core = SimpleNamespace(
        get=AsyncMock(
            return_value={
                "product_id": product_id,
                "status": "active",
                "unit": "piece",
                "product_category": "laptop",
            }
        )
    )
    offers = [
        {
            "supplier_id": s,
            "is_available": True,
            "min_order_qty": Decimal(1),
            "current_unit_price": Decimal(10),
            "currency": "VND",
            "quoted_lead_days": 4,
        }
        for s in [a, b, blocked]
    ]
    suppliers = [
        {
            "supplier_id": s,
            "status": "blocked" if s == blocked else "active",
            "supplier_name": str(s),
        }
        for s in [a, b, blocked]
    ]
    core.rows = AsyncMock(
        side_effect=lambda name, *args, **kwargs: (
            offers if name == "supplier_products" else suppliers
        )
    )
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            version="test",
            source_kind="observed",
            group_ids=set(),
            quantity_references={str(product_id): {"quantity": "100", "unit": "piece"}},
        ),
        ensure_ready=lambda *_: None,
        predict=AsyncMock(return_value=[0.8, 0.2]),
    )
    repo = SimpleNamespace(core=core, facts=AsyncMock(return_value=[]))
    service = PredictionService(repo, runtime, settings())
    response = await service.rank(
        RankRequest(
            product_id=product_id,
            quantity="100",
            requested_delivery_date=(datetime.now(UTC) + timedelta(days=14)).date(),
        )
    )
    assert response.candidates[0].supplier_id == b
    assert response.candidates[0].result.risk_score == 0.2
    assert response.excluded[0]["reason_codes"] == ["SUPPLIER_INACTIVE"]
    runtime.predict.assert_awaited_once()
    assert len(runtime.predict.call_args.args[0]) == 2
    assert not response.trace.persisted
    assert response.trace.prediction_id is None


@pytest.mark.asyncio
async def test_service_authentication_reuses_active_database_policy():
    policy = SimpleNamespace(
        authenticate=AsyncMock(),
        authorize=lambda *a, **k: (_ for _ in ()).throw(CoreError(ErrorCode.FORBIDDEN)),
        has=lambda _: False,
    )
    service = PredictionService(SimpleNamespace(core=None), None, settings())
    service.policy = policy
    with pytest.raises(CoreError) as error:
        await service.authorize(VerifiedIdentity("https://issuer.invalid", "subject"), write=True)
    assert error.value.code == ErrorCode.FORBIDDEN
    policy.authenticate.assert_awaited_once()


@pytest.mark.asyncio
async def test_custom_node_client_uses_fixed_endpoint_and_sanitizes_error():
    seen = []

    async def upstream(request):
        seen.append(request)
        return httpx.Response(503, json={"code": "MODEL_UNAVAILABLE", "message": "secret-upstream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        client = SupplierMLClient(http, "http://localhost:8000", "secret-token")
        with pytest.raises(MLClientError) as error:
            await client.predict(
                ScenarioRequest(
                    product_id=uuid4(),
                    supplier_id=uuid4(),
                    quantity="100",
                    requested_delivery_date="2026-12-01",
                )
            )
    assert seen[0].url.path == "/api/v1/ml/scenarios/predict"
    assert seen[0].headers["authorization"] == "Bearer secret-token"
    assert "secret-token" not in str(error.value)
    assert "secret-upstream" not in str(error.value)
