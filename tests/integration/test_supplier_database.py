# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Explicit integration suite: real Supabase, no skipped/mocked database operations.
# Authentication is injected only for SQL isolation; browser SSO has its own real-IdP suite.
import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowbridge.db import create_engine, get_session
from flowbridge.identity.account_repository import IdentityAccountRepository
from flowbridge.identity.audit import IdentityOutbox
from flowbridge.identity.authentication import VerifiedIdentity, authenticated_identity
from flowbridge.identity.bootstrap import bootstrap_identity
from flowbridge.identity.directory import DirectoryIdentity
from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.contract import dto_validator
from flowbridge.supplier_assistant.database_service import BUSINESS_TIMEZONE
from flowbridge.supplier_assistant.repository import SupplierRepository, table

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


def analysis_payloads() -> dict[str, Any]:
    # Test-only synthetic payloads; never imported or served by the application.
    return json.loads(Path(__file__).with_name("analysis-payloads.json").read_text("utf-8"))


class DatabaseCase:
    def __init__(
        self,
        client: httpx.AsyncClient,
        sessions: async_sessionmaker[AsyncSession],
        identities: dict[str, VerifiedIdentity],
        actor: list[str],
        data: dict[str, Any],
    ) -> None:
        self.client, self.sessions = client, sessions
        self.identities, self.actor, self.data = identities, actor, data

    def as_role(self, role: str) -> None:
        self.actor[0] = role

    async def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        extra = {"Idempotency-Key": key or str(uuid4()), **(headers or {})}
        return await self.client.request(method, path, json=body, headers=extra)

    def purchase_input(self) -> dict[str, Any]:
        return {
            "source": "api",
            "notes": "Isolated integration test",
            "items": [
                {
                    "product_id": str(self.data["product_id"]),
                    "quantity": "10.0000",
                    "preferred_supplier_id": str(self.data["supplier_id"]),
                    "currency": "VND",
                    "requested_delivery_date": (date.today() + timedelta(days=14)).isoformat(),
                }
            ],
        }

    async def purchase(self) -> dict[str, Any]:
        self.as_role("EMPLOYEE")
        response = await self.call("POST", "/api/v1/purchase-requests", self.purchase_input())
        assert response.status_code == 201, response.json().get("code")
        dto_validator("PurchaseRequest").validate(response.json())
        return response.json()


@pytest_asyncio.fixture
async def case() -> AsyncIterator[DatabaseCase]:
    settings = Settings()
    assert settings.database_url is not None, "Integration requires configured .env/database"
    engine = create_engine(settings, use_null_pool=True)
    async with engine.connect() as connection:
        outer = await connection.begin()
        sessions = async_sessionmaker(
            connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            async with sessions() as session, session.begin():
                repo = SupplierRepository(session)
                versions = (
                    (
                        await session.execute(
                            sa.text("SELECT version_num FROM public.alembic_version")
                        )
                    )
                    .scalars()
                    .all()
                )
                heads = set(
                    ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads()
                )
                assert heads and set(versions) == heads, "DB revision differs from source head"
                uid = uuid4().hex
                timestamp = datetime.now(UTC)
                dept = await repo.insert(
                    "departments",
                    {
                        "department_id": uuid4(),
                        "department_code": "TEST-" + uid,
                        "department_name": "Integration",
                        "status": "active",
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    },
                )
                identities, users = {}, {}
                roles = {r["role_code"]: r for r in await repo.rows("roles")}
                admin_id = uuid4()
                for code in [
                    "ADMIN",
                    "EMPLOYEE",
                    "APPROVER",
                    "WAREHOUSE",
                    "AI_SERVICE",
                    "PO_SERVICE",
                    "OTHER",
                ]:
                    user_id = admin_id if code == "ADMIN" else uuid4()
                    identities[code] = VerifiedIdentity(
                        "https://integration.invalid/" + uid,
                        code,
                        account_type="service" if code.endswith("_SERVICE") else "human",
                    )
                    user = await repo.insert(
                        "users",
                        {
                            "user_id": user_id,
                            "external_issuer": identities[code].issuer,
                            "external_subject": code,
                            "display_name": "Integration " + code,
                            "email": None,
                            "department_id": dept["department_id"],
                            "account_type": "service" if code.endswith("_SERVICE") else "human",
                            "status": "active",
                            "created_at": timestamp,
                            "updated_at": timestamp,
                        },
                    )
                    users[code] = user
                    role = roles["EMPLOYEE" if code == "OTHER" else code]
                    await repo.insert(
                        "user_roles",
                        {
                            "user_role_id": uuid4(),
                            "user_id": user_id,
                            "role_id": role["role_id"],
                            "scope_type": role["scope_type"],
                            "department_id": dept["department_id"]
                            if role["scope_type"] == "department"
                            else None,
                            "granted_by": admin_id,
                            "granted_at": timestamp,
                            "revoked_by": None,
                            "revoked_at": None,
                        },
                    )
                product = await repo.one("products", table("products").c.status == "active")
                supplier = await repo.one("suppliers", table("suppliers").c.status == "active")
                quote = await repo.one("supplier_products")
                product_id, supplier_id = uuid4(), uuid4()
                await repo.insert(
                    "products",
                    {
                        **product,
                        "product_id": product_id,
                        "product_code": "TEST-" + uid,
                        "product_category": "office",
                    },
                )
                await repo.insert(
                    "suppliers",
                    {**supplier, "supplier_id": supplier_id, "supplier_code": "TEST-" + uid},
                )
                await repo.insert(
                    "supplier_products",
                    {
                        **quote,
                        "supplier_product_id": uuid4(),
                        "supplier_id": supplier_id,
                        "product_id": product_id,
                        "current_unit_price": "12.3456",
                        "currency": "VND",
                        "min_order_qty": "1.0000",
                        "is_available": True,
                    },
                )
            app = create_app(settings)
            actor = ["EMPLOYEE"]

            async def identity() -> VerifiedIdentity:
                return identities[actor[0]]

            async def session_dependency() -> AsyncIterator[AsyncSession]:
                async with sessions() as session:
                    yield session

            app.dependency_overrides[authenticated_identity] = identity
            app.dependency_overrides[get_session] = session_dependency
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://integration"
                ) as client:
                    yield DatabaseCase(
                        client,
                        sessions,
                        identities,
                        actor,
                        {
                            "department_id": dept["department_id"],
                            "users": users,
                            "roles": roles,
                            "product_id": product_id,
                            "supplier_id": supplier_id,
                        },
                    )
        finally:
            await outer.rollback()
    await engine.dispose()


async def test_catalog_reads_real_rows_with_stable_pagination(case: DatabaseCase) -> None:
    for path in ("suppliers", "products", "supplier-products", "departments"):
        response = await case.client.get(f"/api/v1/{path}?page_size=100")
        assert response.status_code == 200
        assert response.json()["total"] > 1
    response = await case.client.get("/api/v1/products?page=100000&page_size=1")
    assert response.status_code == 200
    assert response.json()["items"] == [] and response.json()["total"] > 1


async def test_create_get_prices_outbox_and_durable_retry(case: DatabaseCase) -> None:
    body, key = case.purchase_input(), str(uuid4())
    created = await case.call("POST", "/api/v1/purchase-requests", body, key=key)
    assert created.status_code == 201, created.json().get("code")
    row = created.json()
    assert row["items"][0]["unit_price_snapshot"] == "12.3456"
    assert row["items"][0]["line_total"] == "123.4560"
    fetched = await case.client.get("/api/v1/purchase-requests/" + row["request_id"])
    assert fetched.status_code == 200 and fetched.json() == row
    replay = await case.call("POST", "/api/v1/purchase-requests", body, key=key)
    assert replay.status_code == 201 and replay.json() == row
    async with case.sessions() as session:
        repo = SupplierRepository(session)
        assert (
            await repo.count(
                "purchase_requests",
                table("purchase_requests").c.request_id == UUID(row["request_id"]),
            )
            == 1
        )
        events = await repo.rows(
            "outbox_events", table("outbox_events").c.aggregate_id == UUID(row["request_id"])
        )
        assert len(events) == 1 and events[0]["status"] == "pending"
    body["notes"] = "Changed input with same key"
    conflict = await case.call("POST", "/api/v1/purchase-requests", body, key=key)
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


async def test_ownership_and_role_headers_cannot_select_actor(case: DatabaseCase) -> None:
    row = await case.purchase()
    case.as_role("OTHER")
    hidden = await case.client.get(
        "/api/v1/purchase-requests/" + row["request_id"],
        headers={"X-User-Id": row["requested_by"], "X-Role": "ADMIN"},
    )
    assert hidden.status_code == 404
    rows = await case.client.get("/api/v1/purchase-requests")
    assert rows.status_code == 200 and rows.json()["total"] == 0
    admin = await case.client.get("/admin/v1/users", headers={"X-Role": "ADMIN"})
    assert admin.status_code == 403


async def test_invalid_second_line_rolls_back_header_and_outbox(case: DatabaseCase) -> None:
    async with case.sessions() as session:
        repo = SupplierRepository(session)
        before = await repo.count("purchase_requests")
        events_before = await repo.count("outbox_events")
    body = case.purchase_input()
    body["items"].append({**body["items"][0], "product_id": str(uuid4())})
    response = await case.call("POST", "/api/v1/purchase-requests", body)
    assert response.status_code == 404
    async with case.sessions() as session:
        repo = SupplierRepository(session)
        assert await repo.count("purchase_requests") == before
        assert await repo.count("outbox_events") == events_before


async def test_edit_checks_revision_and_retains_line_identity(case: DatabaseCase) -> None:
    row = await case.purchase()
    async with case.sessions() as session, session.begin():
        await SupplierRepository(session).update(
            "purchase_requests", UUID(row["request_id"]), {"status": "needs_review"}
        )
    body = {"items": case.purchase_input()["items"], "expected_input_revision": 99}
    url = "/api/v1/purchase-requests/" + row["request_id"]
    stale = await case.call("PATCH", url, body)
    assert stale.status_code == 409 and stale.json()["code"] == "REQUEST_CHANGED"
    body["expected_input_revision"] = 1
    body["items"][0]["quantity"] = "2.5000"
    updated = await case.call("PATCH", url, body)
    assert updated.status_code == 200
    assert updated.json()["input_revision"] == 2
    assert updated.json()["items"][0]["request_item_id"] == row["items"][0]["request_item_id"]
    assert updated.json()["items"][0]["line_total"] == "30.8640"


async def test_analysis_state_revision_and_atomic_lineage(case: DatabaseCase) -> None:
    request = await case.purchase()
    task_response = await case.call(
        "POST",
        "/api/v1/analysis-jobs",
        {"request_id": request["request_id"], "input_revision": 1, "task_type": "reanalysis"},
    )
    assert task_response.status_code == 202, task_response.json().get("code")
    task = task_response.json()
    assert task["input_snapshot"] == request
    duplicate = await case.call(
        "POST",
        "/api/v1/analysis-jobs",
        {"request_id": request["request_id"], "input_revision": 1, "task_type": "reanalysis"},
    )
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "ANALYSIS_IN_PROGRESS"
    case.as_role("AI_SERVICE")
    claim = await case.call(
        "POST", f"/internal/v1/analysis-jobs/{task['task_id']}/claim", {"expected_status": "queued"}
    )
    assert claim.status_code == 200 and claim.json()["status"] == "running"
    bad = await case.call(
        "PUT",
        f"/internal/v1/analysis-jobs/{task['task_id']}/failure",
        {"input_revision": 2, "error_code": "TEST", "error_message": "Test"},
    )
    assert bad.status_code == 409
    failed = await case.call(
        "PUT",
        f"/internal/v1/analysis-jobs/{task['task_id']}/failure",
        {"input_revision": 1, "error_code": "TEST", "error_message": "Test"},
    )
    assert failed.status_code == 200 and failed.json()["status"] == "failed"
    case.as_role("EMPLOYEE")
    fetched = await case.client.get("/api/v1/purchase-requests/" + request["request_id"])
    assert fetched.json()["status"] == "analysis_failed"


async def completed_analysis(
    case: DatabaseCase,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    request = await case.purchase()
    task_response = await case.call(
        "POST",
        "/api/v1/analysis-jobs",
        {"request_id": request["request_id"], "input_revision": 1, "task_type": "reanalysis"},
    )
    assert task_response.status_code == 202
    task = task_response.json()
    case.as_role("AI_SERVICE")
    claim = await case.call(
        "POST", f"/internal/v1/analysis-jobs/{task['task_id']}/claim", {"expected_status": "queued"}
    )
    assert claim.status_code == 200
    fixtures = analysis_payloads()
    prediction, report, candidate = (
        fixtures[name] for name in ("Prediction", "RiskAnalysisReport", "AnalysisSupplierCandidate")
    )
    prediction.update(
        prediction_id=str(uuid4()),
        task_id=task["task_id"],
        request_id=request["request_id"],
        request_item_id=request["items"][0]["request_item_id"],
        supplier_id=str(case.data["supplier_id"]),
        product_id=str(case.data["product_id"]),
        quantity="10.0000",
        requested_delivery_date=request["items"][0]["requested_delivery_date"],
        feature_asof=task["feature_asof"],
        idempotency_key=str(uuid4()),
        predicted_at=datetime.now(UTC).isoformat(),
    )
    anchor = datetime.fromisoformat(task["feature_asof"]).astimezone(BUSINESS_TIMEZONE).date()
    lead_days = (date.fromisoformat(prediction["requested_delivery_date"]) - anchor).days
    prediction["requested_lead_days"] = lead_days
    prediction["feature_snapshot"]["x"]["days_to_deadline"] = lead_days
    prediction["feature_snapshot"]["x"]["supplier_id"] = prediction["supplier_id"]
    report.update(
        report_id=str(uuid4()),
        task_id=task["task_id"],
        request_id=request["request_id"],
        primary_prediction_id=prediction["prediction_id"],
        created_at=datetime.now(UTC).isoformat(),
    )
    candidate.update(
        candidate_id=str(uuid4()),
        report_id=report["report_id"],
        task_id=task["task_id"],
        request_id=request["request_id"],
        request_item_id=prediction["request_item_id"],
        supplier_id=prediction["supplier_id"],
        prediction_id=prediction["prediction_id"],
        unit_price_snapshot="12.3456",
    )
    result = await case.call(
        "PUT",
        f"/internal/v1/analysis-jobs/{task['task_id']}/result",
        {
            "input_revision": 1,
            "report": report,
            "predictions": [prediction],
            "candidates": [candidate],
        },
    )
    assert result.status_code == 200, result.json().get("code")
    return request, task, report


async def test_completed_analysis_creates_persistent_proposal_and_pending_outbox(
    case: DatabaseCase,
) -> None:
    request, task, report = await completed_analysis(case)
    response = await case.call(
        "POST",
        "/internal/v1/proposals",
        {
            "generation_task_id": task["task_id"],
            "request_id": request["request_id"],
            "input_revision": 1,
            "analysis_report_id": report["report_id"],
            "selection_policy_version": "synthetic-selection-v1",
        },
    )
    assert response.status_code == 201, response.json().get("code")
    proposal = response.json()
    assert len(proposal["items"]) == 1 and proposal["workflow_instance_id"] is None
    case.as_role("EMPLOYEE")
    fetched = await case.client.get("/api/v1/proposals/" + proposal["proposal_id"])
    assert fetched.status_code == 200 and fetched.json() == proposal
    case.as_role("APPROVER")
    unavailable = await case.call(
        "POST",
        f"/api/v1/proposals/{proposal['proposal_id']}/decisions",
        {
            "decision": "approved",
            "expected_version_no": 1,
            "input_revision": 1,
            "workflow_instance_id": "not-an-engine",
            "workflow_task_id": "not-authoritative",
        },
    )
    assert unavailable.status_code == 503


async def issued_order(case: DatabaseCase) -> dict[str, Any]:
    request, task, report = await completed_analysis(case)
    proposed = await case.call(
        "POST",
        "/internal/v1/proposals",
        {
            "generation_task_id": task["task_id"],
            "request_id": request["request_id"],
            "input_revision": 1,
            "analysis_report_id": report["report_id"],
            "selection_policy_version": "synthetic-selection-v1",
        },
    )
    assert proposed.status_code == 201
    proposal = proposed.json()
    # Setup only: simulate an already authoritative approval; no workflow engine is asserted.
    async with case.sessions() as session, session.begin():
        repo = SupplierRepository(session)
        await repo.update("proposals", UUID(proposal["proposal_id"]), {"status": "approved"})
        await repo.update("purchase_requests", UUID(request["request_id"]), {"status": "approved"})
    case.as_role("PO_SERVICE")
    response = await case.call(
        "POST",
        "/internal/v1/purchase-orders",
        {
            "approved_proposal_id": proposal["proposal_id"],
            "expected_version_no": 1,
            "input_revision": 1,
        },
    )
    assert response.status_code == 201, response.json().get("code")
    orders = response.json()["purchase_orders"]
    assert len(orders) == 1 and orders[0]["total_amount"] == "123.4560"
    return orders[0]


async def test_order_issuance_and_receipt_limits_persist_state(case: DatabaseCase) -> None:
    order = await issued_order(case)
    case.as_role("WAREHOUSE")
    path = f"/api/v1/purchase-orders/{order['purchase_order_id']}/receipts"
    body = {
        "schedule_id": order["schedules"][0]["schedule_id"],
        "received_quantity": "11.0000",
        "actual_delivery_date": date.today().isoformat(),
    }
    overflow = await case.call("POST", path, body)
    assert overflow.status_code == 409
    body["received_quantity"] = "10.0000"
    received = await case.call("POST", path, body)
    assert received.status_code == 201, received.json().get("code")
    receipt_list = await case.client.get(path)
    assert receipt_list.status_code == 200 and receipt_list.json()["total"] == 1
    fetched = await case.client.get("/api/v1/purchase-orders/" + order["purchase_order_id"])
    assert fetched.status_code == 200 and fetched.json()["status"] == "delivered"
    again = await case.call("POST", path, body)
    assert again.status_code == 409


async def test_order_bound_token_one_use_and_confirmation_revisions(case: DatabaseCase) -> None:
    order = await issued_order(case)
    secret = uuid4().hex
    async with case.sessions() as session, session.begin():
        await SupplierRepository(session).insert(
            "confirmation_tokens",
            {
                "token_id": uuid4(),
                "purchase_order_id": order["purchase_order_id"],
                "token_hash": hashlib.sha256(secret.encode()).hexdigest(),
                "issued_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "consumed_at": None,
                "revoked_at": None,
            },
        )
    path = "/api/v1/supplier-confirmations/" + order["purchase_order_id"]
    headers = {"X-Confirmation-Token": secret}
    preview = await case.client.get(path, headers=headers)
    assert preview.status_code == 200
    invalid = await case.client.get(path, headers={"X-Confirmation-Token": uuid4().hex})
    assert invalid.status_code == 401
    body = {
        "decision": "confirmed",
        "items": [
            {
                "schedule_id": order["schedules"][0]["schedule_id"],
                "confirmed_quantity": "10.0000",
                "promised_delivery_date": date.today().isoformat(),
            }
        ],
    }
    key = str(uuid4())
    response = await case.call("POST", path, body, key=key, headers=headers)
    assert response.status_code == 201, response.json().get("code")
    replay = await case.call("POST", path, body, key=key, headers=headers)
    assert replay.status_code == 201 and replay.json() == response.json()
    consumed = await case.call("POST", path, body, headers=headers)
    assert consumed.status_code == 401
    confirmation = response.json()
    case.as_role("PO_SERVICE")
    detail = await case.call(
        "POST",
        f"/internal/v1/supplier-confirmations/{confirmation['confirmation_id']}/items",
        {"expected_revision_no": 1, "item": body["items"][0]},
    )
    assert detail.status_code == 201 and detail.json()["revision_no"] == 2


async def test_admin_grant_revoke_and_disabled_user_enforced(case: DatabaseCase) -> None:
    case.as_role("ADMIN")
    user_id = str(case.data["users"]["EMPLOYEE"]["user_id"])
    role_id = str(case.data["roles"]["WAREHOUSE"]["role_id"])
    grant_body = {
        "role_id": role_id,
        "scope_type": "department",
        "department_id": str(case.data["department_id"]),
    }
    grant = await case.call("POST", f"/admin/v1/users/{user_id}/roles", grant_body)
    assert grant.status_code == 201, grant.json().get("code")
    duplicate = await case.call("POST", f"/admin/v1/users/{user_id}/roles", grant_body)
    assert duplicate.status_code == 409
    revoked = await case.call(
        "POST", f"/admin/v1/user-roles/{grant.json()['user_role_id']}/revoke", {"reason": "Test"}
    )
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
    disable = await case.call("PATCH", f"/admin/v1/users/{user_id}", {"status": "disabled"})
    assert disable.status_code == 200
    case.as_role("EMPLOYEE")
    denied = await case.client.get("/api/v1/products")
    assert denied.status_code == 403


async def test_absent_model_reports_503_and_lookup_reads_database(case: DatabaseCase) -> None:
    case.as_role("AI_SERVICE")
    result = await case.call(
        "POST",
        "/internal/v1/tools/data-lookup",
        {"resource": "products", "product_id": str(case.data["product_id"])},
    )
    assert result.status_code == 200 and result.json()["total"] == 1
    assert result.json()["items"][0]["product_id"] == str(case.data["product_id"])
    unavailable = await case.call(
        "POST", "/internal/v1/tools/retrieve-knowledge", {"query": "Test"}
    )
    assert unavailable.status_code == 503 and unavailable.json()["code"] == "UPSTREAM_UNAVAILABLE"


async def test_profile_admin_catalog_and_revoked_grant_replay(case: DatabaseCase) -> None:
    profile = await case.client.get("/api/v1/me")
    assert profile.status_code == 200
    assert profile.json()["user"]["user_id"] == str(case.data["users"]["EMPLOYEE"]["user_id"])
    assert {r["role_code"] for r in profile.json()["roles"]} == {"EMPLOYEE"}
    body, key = case.purchase_input(), str(uuid4())
    created = await case.call("POST", "/api/v1/purchase-requests", body, key=key)
    assert created.status_code == 201
    case.as_role("ADMIN")
    for path in ("/admin/v1/users", "/admin/v1/roles"):
        response = await case.client.get(path)
        assert response.status_code == 200 and response.json()["total"] >= 6
    user_id = case.data["users"]["EMPLOYEE"]["user_id"]
    grants = await case.client.get(f"/admin/v1/users/{user_id}/roles")
    assert grants.status_code == 200 and grants.json()["total"] == 1
    revoked = await case.call(
        "POST",
        f"/admin/v1/user-roles/{grants.json()['items'][0]['user_role_id']}/revoke",
        {"reason": "Test current permissions on replay"},
    )
    assert revoked.status_code == 200
    case.as_role("EMPLOYEE")
    denied = await case.call("POST", "/api/v1/purchase-requests", body, key=key)
    assert denied.status_code == 403


async def test_submit_cancel_and_automatic_registration_use_authoritative_event(
    case: DatabaseCase,
) -> None:
    request = await case.purchase()
    async with case.sessions() as session, session.begin():
        await SupplierRepository(session).update(
            "purchase_requests", UUID(request["request_id"]), {"status": "draft"}
        )
    path = "/api/v1/purchase-requests/" + request["request_id"]
    submitted = await case.call("POST", path + "/submit", {"expected_input_revision": 1})
    assert submitted.status_code == 200 and submitted.json()["status"] == "analysing"
    async with case.sessions() as session:
        events = await SupplierRepository(session).rows(
            "outbox_events",
            table("outbox_events").c.aggregate_id == UUID(request["request_id"]),
            table("outbox_events").c.event_type == "purchase_request.submitted",
        )
    case.as_role("AI_SERVICE")
    body = {
        "request_id": request["request_id"],
        "input_revision": 1,
        "task_type": "initial_analysis",
        "source_event_id": str(events[0]["event_id"]),
        "feature_asof": events[0]["created_at"].isoformat(),
        "input_snapshot": submitted.json(),
    }
    task = await case.call("POST", "/internal/v1/analysis-jobs", body)
    assert task.status_code == 202, task.json().get("code")
    case.as_role("EMPLOYEE")
    read = await case.client.get("/api/v1/analysis-jobs/" + task.json()["task_id"])
    assert read.status_code == 200 and read.json() == task.json()
    async with case.sessions() as session, session.begin():
        await SupplierRepository(session).update(
            "purchase_requests", UUID(request["request_id"]), {"status": "needs_review"}
        )
    cancelled = await case.call("POST", path + "/cancel", {"expected_input_revision": 1})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    edit = await case.call(
        "PATCH", path, {"items": case.purchase_input()["items"], "expected_input_revision": 1}
    )
    assert edit.status_code == 409


async def test_email_dispatch_and_retry_never_retry_unknown_delivery(case: DatabaseCase) -> None:
    order = await issued_order(case)
    fixture = analysis_payloads()["EmailLog"]
    fixture.update(
        email_log_id=str(uuid4()),
        purchase_order_id=order["purchase_order_id"],
        supplier_id=order["supplier_id"],
        idempotency_key=str(uuid4()),
        provider_idempotency_key=str(uuid4()),
        status="sending",
    )
    async with case.sessions() as session, session.begin():
        await SupplierRepository(session).insert("email_logs", fixture)
    dispatch_path = f"/internal/v1/purchase-orders/{order['purchase_order_id']}/dispatch-result"
    result = await case.call(
        "PUT",
        dispatch_path,
        {
            "email_log_id": fixture["email_log_id"],
            "status": "failed",
            "error_message": "Test failure",
        },
    )
    assert result.status_code == 200 and result.json()["status"] == "failed"
    case.as_role("EMPLOYEE")
    retry_path = f"/api/v1/purchase-orders/{order['purchase_order_id']}/email-retries"
    retry = await case.call("POST", retry_path, {"reason": "Test retry"})
    assert retry.status_code == 202 and retry.json()["status"] == "queued"
    async with case.sessions() as session, session.begin():
        await SupplierRepository(session).update(
            "email_logs", UUID(fixture["email_log_id"]), {"status": "sending"}
        )
    case.as_role("PO_SERVICE")
    unknown = await case.call(
        "PUT",
        dispatch_path,
        {"email_log_id": fixture["email_log_id"], "status": "delivery_unknown"},
    )
    assert unknown.status_code == 200 and unknown.json()["next_retry_at"] is None
    case.as_role("EMPLOYEE")
    denied = await case.call("POST", retry_path, {"reason": "Must reconcile first"})
    assert denied.status_code == 409


async def test_concurrent_retry_commits_once_visible_on_independent_connection() -> None:
    settings = Settings()
    assert settings.database_url is not None
    engine = create_engine(settings, use_null_pool=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id, dept_id = uuid4(), uuid4()
    identity = VerifiedIdentity("https://integration.invalid/committed", str(user_id))
    try:
        async with sessions() as session, session.begin():
            repo = SupplierRepository(session)
            role = await repo.one("roles", table("roles").c.role_code == "EMPLOYEE")
            product = await repo.one("products", table("products").c.status == "active")
            await repo.insert(
                "departments",
                {
                    "department_id": dept_id,
                    "department_code": "TEST-" + user_id.hex,
                    "department_name": "Committed API test",
                    "status": "active",
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                },
            )
            await repo.insert(
                "users",
                {
                    "user_id": user_id,
                    "external_issuer": identity.issuer,
                    "external_subject": identity.subject,
                    "display_name": "Committed API test",
                    "email": None,
                    "department_id": dept_id,
                    "account_type": "human",
                    "status": "active",
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                },
            )
            await repo.insert(
                "user_roles",
                {
                    "user_role_id": uuid4(),
                    "user_id": user_id,
                    "role_id": role["role_id"],
                    "scope_type": "global",
                    "department_id": None,
                    "granted_by": user_id,
                    "granted_at": datetime.now(UTC),
                    "revoked_by": None,
                    "revoked_at": None,
                },
            )
        app = create_app(settings)

        async def test_identity() -> VerifiedIdentity:
            return identity

        app.dependency_overrides[authenticated_identity] = test_identity
        body = {
            "source": "api",
            "items": [
                {
                    "product_id": str(product["product_id"]),
                    "quantity": "1.0000",
                    "currency": "VND",
                    "requested_delivery_date": (date.today() + timedelta(days=14)).isoformat(),
                }
            ],
        }
        key = str(uuid4())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://integration"
            ) as client:
                first, second = await asyncio.gather(
                    *[
                        client.post(
                            "/api/v1/purchase-requests", json=body, headers={"Idempotency-Key": key}
                        )
                        for _ in range(2)
                    ]
                )
                assert first.status_code == second.status_code == 201
                assert first.json() == second.json()
                async with sessions() as session:
                    repo = SupplierRepository(session)
                    committed = await repo.rows(
                        "purchase_requests", table("purchase_requests").c.requested_by == user_id
                    )
                    assert len(committed) == 1
                    assert str(committed[0]["request_id"]) == first.json()["request_id"]
                fetched = await client.get(
                    "/api/v1/purchase-requests/" + first.json()["request_id"]
                )
                assert fetched.status_code == 200 and fetched.json() == first.json()
                # Test the actual authentication dependency too; no configured IdP bypass exists.
                app.dependency_overrides.clear()
                denied = await client.get("/api/v1/products")
                assert denied.status_code == 401
    finally:
        # Only the random test user's own records are removed, including on assertion failures.
        async with sessions() as session, session.begin():
            requests = sa.select(table("purchase_requests").c.request_id).where(
                table("purchase_requests").c.requested_by == user_id
            )
            await session.execute(
                sa.delete(table("api_idempotency")).where(
                    table("api_idempotency").c.scope.like(str(user_id) + ":%")
                )
            )
            await session.execute(
                sa.delete(table("outbox_events")).where(
                    table("outbox_events").c.aggregate_id.in_(requests)
                )
            )
            await session.execute(
                sa.delete(table("purchase_request_items")).where(
                    table("purchase_request_items").c.request_id.in_(requests)
                )
            )
            await session.execute(
                sa.delete(table("purchase_requests")).where(
                    table("purchase_requests").c.requested_by == user_id
                )
            )
            await session.execute(
                sa.delete(table("user_roles")).where(table("user_roles").c.user_id == user_id)
            )
            await session.execute(
                sa.delete(table("users")).where(table("users").c.user_id == user_id)
            )
            await session.execute(
                sa.delete(table("departments")).where(
                    table("departments").c.department_id == dept_id
                )
            )
        await engine.dispose()


async def test_bootstrap_retries_keep_profile_grants_and_audit_idempotent(
    case: DatabaseCase,
) -> None:
    # SQL integration for the operator boundary; directory metadata is injected only in this test.
    identity = DirectoryIdentity(
        "https://integration.invalid/bootstrap", str(uuid4()), "human", "Bootstrap test", None
    )
    async with case.sessions() as session, session.begin():
        repo = SupplierRepository(session)
        args = dict(
            roles=["EMPLOYEE"],
            department_id=case.data["department_id"],
            granted_by=case.data["users"]["ADMIN"]["user_id"],
        )
        first = await bootstrap_identity(IdentityAccountRepository(session), identity, **args)
        again = await bootstrap_identity(
            IdentityAccountRepository(session), identity, display_name="must not overwrite", **args
        )
        assert first["created"] and first["grants_added"] == 1
        assert again == {"user_id": first["user_id"], "created": False, "grants_added": 0}
        user = await repo.get("users", first["user_id"])
        assert user["display_name"] == "Bootstrap test"
        assert await repo.count("user_roles", table("user_roles").c.user_id == user["user_id"]) == 1
        assert (
            await repo.count(
                "outbox_events", table("outbox_events").c.aggregate_id == user["user_id"]
            )
            == 1
        )


async def test_first_admin_cannot_bypass_an_existing_admin_or_grant_as_employee(
    case: DatabaseCase,
) -> None:
    identity = DirectoryIdentity(
        "https://integration.invalid/bootstrap", str(uuid4()), "human", "Denied", None
    )
    async with case.sessions() as session, session.begin():
        repo = SupplierRepository(session)
        for kwargs in (
            dict(roles=["ADMIN"], first_admin=True),
            dict(roles=["ADMIN"], granted_by=case.data["users"]["EMPLOYEE"]["user_id"]),
        ):
            with pytest.raises(CoreError) as failure:
                await bootstrap_identity(IdentityAccountRepository(session), identity, **kwargs)
            assert failure.value.code == ErrorCode.FORBIDDEN
        assert await repo.count("users", table("users").c.external_subject == identity.subject) == 0


async def test_bootstrap_dry_run_transaction_leaves_no_user_grant_or_outbox(
    case: DatabaseCase,
) -> None:
    identity = DirectoryIdentity(
        "https://integration.invalid/dry-run", str(uuid4()), "human", "Dry run", None
    )
    async with case.sessions() as session, session.begin():
        repo = SupplierRepository(session)
        async with session.begin_nested() as transaction:
            result = await bootstrap_identity(
                IdentityAccountRepository(session),
                identity,
                roles=["EMPLOYEE"],
                department_id=case.data["department_id"],
                granted_by=case.data["users"]["ADMIN"]["user_id"],
            )
            assert result["created"]
            await transaction.rollback()
        user_id = UUID(result["user_id"])
        assert await repo.count("users", table("users").c.user_id == user_id) == 0
        assert await repo.count("user_roles", table("user_roles").c.user_id == user_id) == 0
        assert (
            await repo.count("outbox_events", table("outbox_events").c.aggregate_id == user_id) == 0
        )


async def test_bootstrap_audit_failure_rolls_back_identity_grant_and_event(
    case: DatabaseCase,
) -> None:
    identity = DirectoryIdentity(
        "https://integration.invalid/audit-failure", str(uuid4()), "human", "Rollback", None
    )

    class FailingAudit(IdentityOutbox):
        async def bootstrap_applied(self, user_id: UUID, actor_id: UUID, roles: list[str]) -> None:
            await super().bootstrap_applied(user_id, actor_id, roles)
            self.user_id = user_id
            raise RuntimeError("Injected failure after outbox write")

    async with case.sessions() as session, session.begin():
        repo = SupplierRepository(session)
        audit = FailingAudit(session)
        with pytest.raises(RuntimeError, match="Injected failure"):
            async with session.begin_nested():
                await bootstrap_identity(
                    IdentityAccountRepository(session),
                    identity,
                    audit=audit,
                    roles=["EMPLOYEE"],
                    department_id=case.data["department_id"],
                    granted_by=case.data["users"]["ADMIN"]["user_id"],
                )
        assert await repo.count("users", table("users").c.user_id == audit.user_id) == 0
        assert await repo.count("user_roles", table("user_roles").c.user_id == audit.user_id) == 0
        assert (
            await repo.count(
                "outbox_events", table("outbox_events").c.aggregate_id == audit.user_id
            )
            == 0
        )
