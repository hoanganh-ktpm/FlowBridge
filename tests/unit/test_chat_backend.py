# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# SQLite exercises service transactions and policy queries; it does not certify PostgreSQL locks.
import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from flowbridge.main import create_app
from flowbridge.models import Base
from flowbridge.settings import Settings
from flowbridge.supplier_assistant.chat import ChatService, LangflowChatAdapter
from flowbridge.supplier_assistant.repository import table

ISSUER = "https://idp.test/realms/flowbridge"
SECRET = "bridge-secret-" + "s" * 32


def configuration(**overrides):
    return Settings(
        _env_file=None,
        **{
            "environment": "test",
            "database_url": "postgresql+asyncpg://test@localhost/test",
            "chat_enabled": True,
            "oidc_issuer": ISSUER,
            "oidc_audience": "flowbridge-api",
            "oidc_introspection_url": ISSUER + "/protocol/openid-connect/token/introspect",
            "oidc_client_id": "api",
            "oidc_client_secret": "introspection-secret",
            "oidc_allowed_clients": ["web"],
            "langflow_url": "http://langflow.test",
            "langflow_api_key": "native-langflow-key",
            "langflow_bridge_secret": SECRET,
            **overrides,
        },
    )


class AsyncSession:
    def __init__(self, engine):
        self.sync = Session(engine, expire_on_commit=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.sync.close()

    @asynccontextmanager
    async def begin(self):
        with self.sync.begin():
            yield

    async def execute(self, statement):
        return self.sync.execute(statement)


@pytest.fixture
def chat_case():
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    with engine.connect() as connection:
        connection.connection.create_function("pg_advisory_xact_lock", 1, lambda _: 0)
    metadata = sa.MetaData()
    names = {
        "users",
        "roles",
        "user_roles",
        "departments",
        "purchase_requests",
        "purchase_orders",
        "analysis_tasks",
        "chat_sessions",
        "chat_turns",
    }
    for source in Base.metadata.sorted_tables:
        if source.name not in names:
            continue
        target = source.to_metadata(metadata)
        for constraint in list(target.constraints):
            if isinstance(constraint, (sa.CheckConstraint, sa.ForeignKeyConstraint)):
                target.constraints.remove(constraint)
        for column in target.c:
            column.foreign_keys.clear()
            column.server_default = None
            if not column.primary_key:
                column.nullable = True
            if column.name in {"result", "resources"}:
                column.type = sa.JSON()
        target.indexes.clear()
    metadata.create_all(engine)
    actor, other, role, grant = uuid4(), uuid4(), uuid4(), uuid4()
    request, other_request = uuid4(), uuid4()
    with engine.begin() as connection:
        for identifier, subject in ((actor, "alice"), (other, "bob")):
            connection.execute(
                sa.insert(table("users")).values(
                    user_id=identifier,
                    external_issuer=ISSUER,
                    external_subject=subject,
                    account_type="human",
                    status="active",
                )
            )
        connection.execute(
            sa.insert(table("roles")).values(
                role_id=role,
                role_code="EMPLOYEE",
                status="active",
                allowed_account_type="human",
                scope_type="global",
            )
        )
        for identifier, user in ((grant, actor), (uuid4(), other)):
            connection.execute(
                sa.insert(table("user_roles")).values(
                    user_role_id=identifier,
                    user_id=user,
                    role_id=role,
                    scope_type="global",
                    revoked_at=None,
                    department_id=None,
                )
            )
        for identifier, user in ((request, actor), (other_request, other)):
            connection.execute(
                sa.insert(table("purchase_requests")).values(
                    request_id=identifier,
                    requested_by=user,
                    request_code=str(identifier),
                    status="analysing",
                    input_revision=1,
                )
            )
    runs, probes = [], []
    response = {
        "answer": "Đã đọc dữ liệu.",
        "sources": [],
        "warnings": [],
        "needs_clarification": False,
        "request_ids": [],
        "order_ids": [],
    }

    async def upstream(request_http):
        if request_http.url.host == "idp.test":
            import urllib.parse

            token = urllib.parse.parse_qs(request_http.content.decode()).get("token", [""])[0]
            return httpx.Response(
                200,
                json={
                    "active": token in {"alice-token", "bob-token"},
                    "iss": ISSUER,
                    "aud": "flowbridge-api",
                    "sub": token.split("-")[0],
                    "exp": time.time() + 300,
                    "client_id": "web",
                    "token_type": "Bearer",
                },
            )
        if request_http.method == "GET":
            probes.append(request_http)
            return httpx.Response(
                200, json={"protocol": "flowbridge-caller-v1", "flow_id": "flowbridge-ver2-central"}
            )
        runs.append(request_http)
        if isinstance(response, Exception):
            raise response
        return httpx.Response(200, json=response)

    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(configuration())

    def sessions():
        return AsyncSession(engine)

    app.state.resources = SimpleNamespace(sessions=sessions, http=http)
    case = SimpleNamespace(
        app=app,
        engine=engine,
        actor=actor,
        other=other,
        role=role,
        request=request,
        other_request=other_request,
        response=response,
        runs=runs,
        probes=probes,
        http=http,
        sessions=sessions,
    )
    yield case
    engine.dispose()


@pytest.mark.asyncio
async def test_procurement_reader_can_use_company_evidence_but_keeps_private_chat_ownership(
    chat_case,
):
    case, role_id, grant_id = chat_case, uuid4(), uuid4()
    with case.engine.begin() as connection:
        connection.execute(
            sa.insert(table("roles")).values(
                role_id=role_id,
                role_code="PROCUREMENT_VIEWER",
                status="active",
                allowed_account_type="human",
                scope_type="global",
            )
        )
        connection.execute(
            sa.insert(table("user_roles")).values(
                user_role_id=grant_id,
                user_id=case.actor,
                role_id=role_id,
                scope_type="global",
                department_id=None,
                revoked_at=None,
            )
        )
    case.response["request_ids"] = [str(case.other_request)]
    async with (
        case.http,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=case.app), base_url="http://api.test"
        ) as client,
    ):
        headers = {"Authorization": "Bearer alice-token", "Idempotency-Key": "reader"}
        result = await client.post(
            "/api/v1/chat/messages",
            headers=headers,
            json={"message": "Lịch sử giao hàng toàn công ty"},
        )
        assert result.status_code == 200, result.text
        history = f"/api/v1/chat/sessions/{result.json()['session_id']}/messages"
        assert (
            await client.get(history, headers={"Authorization": "Bearer bob-token"})
        ).status_code == 404
        with case.engine.begin() as connection:
            connection.execute(
                sa.delete(table("user_roles")).where(table("user_roles").c.user_role_id == grant_id)
            )
        # Revocation rechecks source permissions on historical messages too.
        assert (await client.get(history, headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_http_real_auth_policy_retry_history_and_isolation(chat_case):
    case = chat_case
    async with (
        case.http,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=case.app), base_url="http://api.test"
        ) as client,
    ):
        path = "/api/v1/chat/messages"
        assert (await client.post(path, json={"message": "hello"})).status_code == 401
        headers = {"Authorization": "Bearer alice-token", "Idempotency-Key": "turn-1"}
        body = {"message": "Đọc yêu cầu", "request_id": str(case.request)}
        result = await client.post(path, headers=headers, json=body)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["answer"] == case.response["answer"]
        assert (await client.post(path, headers=headers, json=body)).json() == data
        assert len(case.runs) == 1
        assert case.runs[0].headers["X-FlowBridge-Access-Token"] == "alice-token"
        assert case.runs[0].headers["x-api-key"] == "native-langflow-key"
        assert case.runs[0].headers["X-FlowBridge-Request-Context"] == str(case.request)
        assert "alice-token" not in case.runs[0].content.decode()
        assert str(case.request) not in case.runs[0].content.decode()
        assert (
            await client.post(path, headers=headers, json={"message": "changed"})
        ).status_code == 409
        history = f"/api/v1/chat/sessions/{data['session_id']}/messages"
        messages = await client.get(history, headers=headers)
        assert messages.status_code == 200, messages.text
        assert [m["role"] for m in messages.json()["items"]] == ["user", "assistant"]
        assert messages.json()["total"] == 2
        second = await client.get(history + "?page=2&page_size=1", headers=headers)
        assert second.json()["items"][0]["role"] == "assistant"
        bob = {**headers, "Authorization": "Bearer bob-token"}
        assert (await client.get(history, headers=bob)).status_code == 404
        assert (
            await client.post(
                path,
                headers={**bob, "Idempotency-Key": "bob"},
                json={"session_id": data["session_id"], "message": "hello"},
            )
        ).status_code == 404
        with case.engine.begin() as connection:
            connection.execute(
                sa.update(table("purchase_requests"))
                .where(table("purchase_requests").c.request_id == case.request)
                .values(requested_by=case.other)
            )
        assert (await client.get(history, headers=headers)).status_code == 404
        assert (await client.post(path, headers=headers, json=body)).status_code == 404
        assert len(case.runs) == 1


@pytest.mark.asyncio
async def test_revoked_grant_and_unknown_request_never_run_model(chat_case):
    case = chat_case
    async with (
        case.http,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=case.app), base_url="http://api.test"
        ) as client,
    ):
        headers = {"Authorization": "Bearer alice-token", "Idempotency-Key": "blocked"}
        result = await client.post(
            "/api/v1/chat/messages",
            headers=headers,
            json={"message": "hello", "request_id": str(case.other_request)},
        )
        assert result.status_code == 404
        with case.engine.begin() as connection:
            connection.execute(sa.update(table("roles")).values(status="inactive"))
        result = await client.post(
            "/api/v1/chat/messages", headers=headers, json={"message": "hello"}
        )
        assert result.status_code == 403
        assert not case.runs


@pytest.mark.asyncio
async def test_invalid_output_is_durable_failure_without_second_execution(chat_case):
    case = chat_case
    case.response["answer"] = "x" * 6001
    async with (
        case.http,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=case.app), base_url="http://api.test"
        ) as client,
    ):
        for _ in range(2):
            result = await client.post(
                "/api/v1/chat/messages",
                json={"message": "hello"},
                headers={"Authorization": "Bearer alice-token", "Idempotency-Key": "failed"},
            )
            assert result.status_code == 503
            assert "x" * 100 not in result.text
        assert len(case.runs) == 1
        with Session(case.engine) as session:
            turn = session.execute(sa.select(table("chat_turns"))).mappings().one()
            assert turn["state"] == "failed"
            assert turn["result"] is None


@pytest.mark.asyncio
async def test_crashed_execution_remains_reserved_and_never_retries(chat_case, monkeypatch):
    from flowbridge.identity.authentication import VerifiedIdentity
    from flowbridge.shared.errors import CoreError, ErrorCode

    case = chat_case
    service = ChatService(case.sessions, case.http, case.app.state.settings)
    attempts = []

    async def interrupted(*args):
        attempts.append(args)
        raise asyncio.CancelledError

    monkeypatch.setattr(service.adapter, "run", interrupted)
    actor = VerifiedIdentity(ISSUER, "alice")
    with pytest.raises(asyncio.CancelledError):
        await service.send(actor, {"message": "hello"}, "crashed", "alice-token")
    with pytest.raises(CoreError) as error:
        await service.send(actor, {"message": "hello"}, "crashed", "alice-token")
    assert error.value.code == ErrorCode.ANALYSIS_IN_PROGRESS
    assert len(attempts) == 1
    await case.http.aclose()


@pytest.mark.asyncio
async def test_stock_langflow_preflight_fails_before_model():
    from flowbridge.shared.errors import CoreError

    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(404, json={"detail": "Not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        with pytest.raises(CoreError):
            await LangflowChatAdapter(http, configuration()).run("hello", "user", uuid4(), None)
    assert [r.method for r in calls] == ["GET"]
    assert "X-FlowBridge-Access-Token" not in calls[0].headers


def test_settings_validate_origin_credentials_and_component_database():
    with pytest.raises(ValidationError):
        configuration(langflow_url="http://attacker.test/run?token=x")
    with pytest.raises(ValidationError):
        configuration(langflow_bridge_secret="short")
    settings = configuration(
        database_url=None,
        database_host="localhost",
        database_user="app",
        database_password="secret",
    )
    assert settings.database_url is not None
    assert SECRET not in repr(settings)


def test_chat_openapi_reports_adapter_and_migration_without_mutating_snapshot():
    from flowbridge.supplier_assistant.contract import application_documents

    before = application_documents()
    document = create_app(configuration(docs_enabled=True)).openapi()
    assert document["x-database-migration"] == "0005_chat_projection"
    assert "Langflow" in document["paths"]["/api/v1/chat/messages"]["post"]["description"]
    assert (
        document["components"]["schemas"]["ChatRequest"]
        == before[0]["components"]["schemas"]["ChatRequest"]
    )
    assert application_documents() == before


@pytest.mark.asyncio
async def test_observed_private_resource_is_reauthorized_for_unbound_session(chat_case):
    case = chat_case
    case.response["request_ids"] = [str(case.request)]
    async with (
        case.http,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=case.app), base_url="http://api.test"
        ) as client,
    ):
        headers = {"Authorization": "Bearer alice-token", "Idempotency-Key": "unbound"}
        result = await client.post("/api/v1/chat/messages", headers=headers, json={"message": "PR"})
        assert result.status_code == 200
        session_id = result.json()["session_id"]
        with case.engine.begin() as connection:
            connection.execute(
                sa.update(table("purchase_requests"))
                .where(table("purchase_requests").c.request_id == case.request)
                .values(requested_by=case.other)
            )
        assert (
            await client.get(f"/api/v1/chat/sessions/{session_id}/messages", headers=headers)
        ).status_code == 404
        assert (
            await client.post("/api/v1/chat/messages", headers=headers, json={"message": "PR"})
        ).status_code == 404
        assert len(case.runs) == 1


@pytest.mark.asyncio
async def test_missing_or_invalid_bearer_never_reaches_langflow(chat_case):
    case = chat_case
    async with (
        case.http,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=case.app), base_url="http://api.test"
        ) as client,
    ):
        for authorization in ("Bearer expired-token", "Bearer id-token", "Basic alice-token"):
            response = await client.post(
                "/api/v1/chat/messages",
                json={"message": "hello"},
                headers={"Authorization": authorization, "Idempotency-Key": "invalid"},
            )
            assert response.status_code == 401
        assert not case.runs and not case.probes


@pytest.mark.asyncio
async def test_rag_task_selected_only_from_owned_bound_request(chat_case):
    from datetime import UTC, datetime

    from flowbridge.identity.authentication import VerifiedIdentity

    case = chat_case
    job, foreign_job = uuid4(), uuid4()
    with case.engine.begin() as connection:
        for task_id, request_id in ((job, case.request), (foreign_job, case.other_request)):
            connection.execute(
                sa.insert(table("analysis_tasks")).values(
                    task_id=task_id,
                    request_id=request_id,
                    input_revision=1,
                    status="running",
                    feature_asof=datetime.now(UTC),
                )
            )
    settings = configuration(rag_postgres_enabled=True)
    service = ChatService(case.sessions, case.http, settings)
    turn, execute = await service.reserve(
        VerifiedIdentity(ISSUER, "alice"),
        {"message": "RAG", "request_id": str(case.request)},
        "rag",
    )
    assert execute
    assert turn["job_context"] == job
    assert turn["job_context"] != foreign_job
    await case.http.aclose()


@pytest.mark.asyncio
async def test_user_token_denied_in_tool_maps_to_auth_error_without_raw_provider_text():
    from flowbridge.shared.errors import CoreError, ErrorCode

    def upstream(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"protocol": "flowbridge-caller-v1", "flow_id": "flowbridge-ver2-central"}
            )
        return httpx.Response(401, headers={"X-FlowBridge-Tool-Denied": "401"}, text="sensitive")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        with pytest.raises(CoreError) as error:
            await LangflowChatAdapter(http, configuration()).run("hello", "expired", uuid4(), None)
    assert error.value.code == ErrorCode.UNAUTHENTICATED
    assert "sensitive" not in str(error.value)
