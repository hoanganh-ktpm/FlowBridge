# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Real PostgreSQL + real TabICL; test users/approval are fixtures, not SSO/workflow evidence.
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from starlette.concurrency import run_in_threadpool
from test_supplier_database import DatabaseCase, issued_order
from test_supplier_database import case as case

from flowbridge.db import create_engine, create_session_factory
from flowbridge.identity.authentication import VerifiedIdentity, authenticated_identity
from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.prediction.model import TabICLRuntime
from flowbridge.supplier_assistant.prediction.repository import PredictionRepository
from flowbridge.supplier_assistant.prediction.router import install_prediction_router
from flowbridge.supplier_assistant.prediction.schemas import RankRequest
from flowbridge.supplier_assistant.prediction.service import PredictionService
from flowbridge.supplier_assistant.repository import SupplierRepository, table

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


async def test_real_model_rank_po_persistence_replay_permissions_and_rollback(case: DatabaseCase):
    order = await issued_order(case)
    order_id = UUID(order["purchase_order_id"])
    config = Settings(
        _env_file=None,
        ml_context_path=ROOT / ".cache/ml/context.json",
        ml_checkpoint_path=ROOT / ".cache/models/tabicl-classifier-v2-20260212.ckpt",
        ml_n_estimators=1,
        ml_cpu_threads=2,
    )
    runtime = TabICLRuntime(config)
    await run_in_threadpool(runtime.load)
    runtime.ensure_ready()
    async with case.sessions() as session, session.begin():
        core = SupplierRepository(session)
        product = await core.get("products", case.data["product_id"])
        runtime.context.quantity_references[str(product["product_id"])] = {
            "unit": product["unit"],
            "quantity": "10",
        }
        service = PredictionService(PredictionRepository(core), runtime, config)
        await service.authorize(case.identities["AI_SERVICE"])
        before = await core.count("risk_predictions")
        ranked = await service.rank(
            RankRequest(
                product_id=product["product_id"],
                quantity="10",
                requested_delivery_date=(datetime.now(UTC) + timedelta(days=14)).date(),
            )
        )
        assert ranked.status == "success"
        assert ranked.candidates[0].supplier_id == case.data["supplier_id"]
        assert 0 <= ranked.candidates[0].result.risk_score <= 1
        assert not ranked.trace.persisted
        assert await core.count("risk_predictions") == before

    # Fixture uses an outer rollback transaction; write HTTP route commits only its savepoint.
    app = case.client._transport.app
    app.state.ml_runtime = runtime
    app.state.settings = app.state.settings.model_copy(update={"ml_enabled": True})
    install_prediction_router(app)
    case.as_role("EMPLOYEE")
    denied = await case.call("POST", f"/internal/v1/ml/purchase-orders/{order_id}/predict")
    assert denied.status_code == 403
    case.as_role("AI_SERVICE")
    url = f"/internal/v1/ml/purchase-orders/{order_id}/predict"
    first = await case.call("POST", url, key="ml-po-integration")
    assert first.status_code == 200, first.json()
    response = first.json()
    assert response["status"] == "success"
    assert response["is_complete"] is True
    assert response["aggregation_policy"] == "remaining_value_weighted_v2"
    assert response["representative_risk_score"] == pytest.approx(
        sum(
            float(item["remaining_value"]) * item["result"]["risk_score"]
            for item in response["items"]
        )
        / sum(float(item["remaining_value"]) for item in response["items"])
    )
    again = await case.call("POST", url, key="ml-po-integration")
    assert again.status_code == 200
    assert again.json() == response
    prediction_id = UUID(response["items"][0]["trace"]["prediction_id"])
    async with case.sessions() as session, session.begin():
        core = SupplierRepository(session)
        saved = await core.get("risk_predictions", prediction_id)
        assert saved["po_item_id"] == UUID(order["items"][0]["po_item_id"])
        assert saved["feature_snapshot"]["x"]["expected_issue_wait_days"] is None
        assert saved["context_metadata"]["checkpoint_sha256"] == runtime.checkpoint_sha256
        count = await core.count(
            "risk_predictions", table("risk_predictions").c.po_item_id == saved["po_item_id"]
        )
        assert count == 1
        service = PredictionService(PredictionRepository(core), runtime, app.state.settings)
        await service.authorize(case.identities["EMPLOYEE"])
        current = await service.current_order_risk(order_id)
        assert current.highest_risk_item_id == saved["po_item_id"]
        assert current.items[0].trace.prediction_id == prediction_id
        await service.authorize(case.identities["OTHER"])
        with pytest.raises(CoreError) as denied:
            await service.prediction(prediction_id)
        assert denied.value.code == ErrorCode.NOT_FOUND

    original_predict = runtime.predict

    async def fail_inference(_snapshots):
        raise CoreError(ErrorCode.MODEL_UNAVAILABLE)

    runtime.predict = fail_inference
    failed = await case.call("POST", url, key="ml-failed-integration")
    assert failed.status_code == 503
    runtime.predict = original_predict
    async with case.sessions() as session, session.begin():
        core = SupplierRepository(session)
        assert (
            await core.count(
                "risk_predictions", table("risk_predictions").c.po_item_id == saved["po_item_id"]
            )
            == 1
        )
        assert (
            await core.count(
                "api_idempotency", table("api_idempotency").c.key == "ml-failed-integration"
            )
            == 0
        )


async def test_ml_http_read_only_routes_real_lifespan():
    # Seeded catalog read only; no test business rows committed or existing rows updated.
    config = Settings(
        ml_enabled=True,
        ml_n_estimators=1,
        ml_cpu_threads=2,
        ml_context_path=ROOT / ".cache/ml/context.json",
        ml_checkpoint_path=ROOT / ".cache/models/tabicl-classifier-v2-20260212.ckpt",
    )
    engine = create_engine(config, use_null_pool=True)
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session, session.begin():
            users, grants, roles = table("users"), table("user_roles"), table("roles")
            principal = (
                (
                    await session.execute(
                        sa.select(users)
                        .join(grants, users.c.user_id == grants.c.user_id)
                        .join(roles, roles.c.role_id == grants.c.role_id)
                        .where(
                            users.c.status == "active",
                            roles.c.role_code == "AI_SERVICE",
                            grants.c.revoked_at.is_(None),
                        )
                        .limit(1)
                    )
                )
                .mappings()
                .one()
            )
            identity = VerifiedIdentity(principal["external_issuer"], principal["external_subject"])
            core = SupplierRepository(session)
            product = await core.one("products", table("products").c.status == "active")
            before = (await core.count("risk_predictions"), await core.count("api_idempotency"))
        app = create_app(config)
        app.dependency_overrides[authenticated_identity] = lambda: identity
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://integration"
            ) as client:
                assert (await client.get("/_ops/ml/ready")).status_code == 200
                payload = {
                    "product_id": str(product["product_id"]),
                    "quantity": "100",
                    "requested_delivery_date": (datetime.now(UTC) + timedelta(days=14))
                    .date()
                    .isoformat(),
                }
                ranked = await client.post("/api/v1/ml/suppliers/rank", json=payload)
                assert ranked.status_code == 200, ranked.json()
                assert ranked.json()["candidates"]
                candidate = ranked.json()["candidates"][0]
                scored = await client.post(
                    "/api/v1/ml/scenarios/predict",
                    json={
                        **payload,
                        "supplier_id": candidate["supplier_id"],
                    },
                )
                assert scored.status_code == 200, scored.json()
                assert scored.json()["result"]["status"] == "success"
                assert scored.json()["trace"]["persisted"] is False
                assert scored.json()["trace"]["prediction_id"] is None
        assert app.state.ml_runtime is None
        async with sessions() as session, session.begin():
            core = SupplierRepository(session)
            after = (await core.count("risk_predictions"), await core.count("api_idempotency"))
            assert after == before
    finally:
        await engine.dispose()
