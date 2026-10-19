# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Policy/HTTP invariants with mocked DB/IAM, separate from actual engine acceptance.
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import time
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
import pytest_asyncio
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.identity.contexts import RegisteredJob, resolve_job_context
from flowbridge.identity.dependencies import identity_service, verify_user_context
from flowbridge.identity.directory import DirectoryIdentity
from flowbridge.identity.repository import Grant, IdentityRecord
from flowbridge.identity.service import IdentityService
from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError

ISSUER = "https://iam.example.invalid/realms/core"
BINDING = {
    "tenant_id": "tenant-a",
    "application_id": "supplier-app",
    "web_client_id": "web",
    "mobile_client_id": "mobile",
    "role_permissions": {
        "EMPLOYEE": ["identity:read", "data:read"],
        "ADMIN": ["identity:read"],
        "APPROVER": ["identity:read", "workflow:act"],
        "AI_SERVICE": ["identity:read", "data:read", "context:delegate"],
    },
    "service_clients": {
        "ai": {
            "role": "AI_SERVICE",
            "permissions": [
                "identity:read",
                "data:read",
                "context:delegate",
            ],
        }
    },
}


def settings(**updates: object) -> Settings:
    values = dict(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://x:y@localhost/test",
        oidc_issuer=ISSUER,
        oidc_audience="core",
        oidc_client_id="api",
        oidc_client_secret="test-only-secret",
        oidc_introspection_url=ISSUER + "/protocol/openid-connect/token/introspect",
        oidc_allowed_clients=["web", "mobile", "ai"],
        oidc_service_clients=["ai"],
        core_identity=BINDING,
    )
    values.update(updates)
    return Settings(**values)


@pytest.mark.parametrize("ml,count", [(False, 48), (True, 54)])
def test_identity_openapi_semantics_match_core_contract(ml: bool, count: int) -> None:
    from fastapi import FastAPI

    app = create_app(settings(ml_enabled=ml))
    runtime = app.openapi()
    contract = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "contracts/opencore-api.yaml").read_text("utf-8")
    )

    def normalize(value: object, document: dict) -> object:
        if isinstance(value, dict):
            if "$ref" in value:
                resolved = document
                for part in value["$ref"].removeprefix("#/").split("/"):
                    resolved = resolved[part]
                return normalize(resolved, document)
            result = {
                key: normalize(item, document)
                for key, item in value.items()
                if key not in {"title", "description", "example", "examples"}
            }
            # Literal True has an implicit boolean type in the canonical schema.
            if result.get("const") is True:
                result.pop("type", None)
            return result
        if isinstance(value, list):
            return [normalize(item, document) for item in value]
        return value

    for name in ("IdentityConfig", "Principal", "ErrorResponse"):
        assert normalize(runtime["components"]["schemas"]["Core" + name], runtime) == normalize(
            contract["components"]["schemas"][name], contract
        )
    for path in ("/api/v1/core/identity/config", "/api/v1/core/identity/me"):
        actual, expected = runtime["paths"][path]["get"], contract["paths"][path]["get"]
        assert actual["operationId"] == expected["operationId"]
        assert actual["security"] == expected.get("security", contract["security"])
        assert normalize(actual["parameters"], runtime) == normalize(
            expected["parameters"], contract
        )
        for status, response in expected["responses"].items():
            assert normalize(actual["responses"][str(status)], runtime) == normalize(
                response, contract
            )
    app.openapi_schema = None
    live = FastAPI.openapi(app)
    for document in (live, runtime):
        operations = {
            (path, method, spec["operationId"])
            for path, entry in document["paths"].items()
            for method, spec in entry.items()
        }
        if document is live:
            live_operations = operations
        else:
            assert operations == live_operations
            assert len(operations) == count


@pytest.mark.parametrize(
    "field", ["issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"]
)
def test_identity_config_rejects_relative_uri(field: str) -> None:
    from flowbridge.identity.schemas import IdentityConfig
    from flowbridge.identity.service import identity_config

    data = identity_config(settings()).model_dump()
    data[field] = "relative/path"
    with pytest.raises(ValueError, match="absolute URI"):
        IdentityConfig.model_validate(data)


class Records:
    def __init__(self) -> None:
        self.users = {
            "human": IdentityRecord("user-1", "Employee", "human", (Grant("EMPLOYEE", None),)),
            "service": IdentityRecord("svc-1", "Worker", "service", (Grant("AI_SERVICE", None),)),
        }

    async def resolve(self, issuer: str, subject: str) -> IdentityRecord:
        assert issuer == ISSUER
        return self.users[subject]


class Directory:
    client_id = "ai"

    async def resolve(self, subject: str) -> DirectoryIdentity:
        assert subject == "service"
        return DirectoryIdentity(ISSUER, subject, "service", "Worker", None, self.client_id)


@pytest_asyncio.fixture
async def core() -> AsyncIterator[tuple]:
    config, records, directory = settings(), Records(), Directory()
    service = IdentityService(config, records, directory)
    claims = {
        "active": True,
        "iss": ISSUER,
        "sub": "human",
        "aud": ["core"],
        "exp": time() + 300,
        "client_id": "web",
        "token_type": "Bearer",
        "scope": "openid identity:read data:read",
    }

    def introspect(request: httpx.Request) -> httpx.Response:
        token = parse_qs(request.content.decode())["token"][0]
        if token != "human-token":
            return httpx.Response(200, json={"active": False})
        return httpx.Response(200, json=claims)

    app = create_app(config)

    async def dependency() -> AsyncIterator[IdentityService]:
        yield service

    app.dependency_overrides[identity_service] = dependency
    async with httpx.AsyncClient(transport=httpx.MockTransport(introspect)) as upstream:
        app.state.resources = SimpleNamespace(http=upstream)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://api.invalid",
        ) as client:
            yield client, claims, records, directory, service, app


@pytest.mark.asyncio
async def test_public_config_and_principal_match_core_schema(core: tuple) -> None:
    client, _, _, _, _, app = core
    headers = {
        "Authorization": "Bearer human-token",
        "X-Tenant-Id": "attacker",
        "X-User-Id": "admin",
        "X-Role": "ADMIN",
        "X-Correlation-Id": "REQ-core",
    }
    config = await client.get("/api/v1/core/identity/config")
    me = await client.get("/api/v1/core/identity/me", headers=headers)
    assert config.status_code == me.status_code == 200
    assert me.headers["x-correlation-id"] == "REQ-core"
    assert me.json() == {
        "id": "user-1",
        "tenant_id": "tenant-a",
        "display_name": "Employee",
        "roles": ["supplier-app:EMPLOYEE"],
        "permissions": ["data:read", "identity:read"],
    }
    assert config.json()["pkce_required"] is True
    assert "test-only-secret" not in config.text
    root = Path(__file__).resolve().parents[2]
    contract = yaml.safe_load((root / "contracts/opencore-api.yaml").read_text("utf-8"))
    uri = "urn:test:core"
    registry = Registry().with_resource(uri, Resource.from_contents(contract, DRAFT202012))
    for response, name in ((config, "IdentityConfig"), (me, "Principal")):
        Draft202012Validator(
            {"$ref": f"{uri}#/components/schemas/{name}"},
            registry=registry,
        ).validate(response.json())
    schema = app.openapi()
    assert schema["paths"]["/api/v1/core/identity/config"]["get"]["security"] == []
    assert schema["paths"]["/api/v1/core/identity/me"]["get"]["security"] == [{"BearerAuth": []}]
    assert len([v for p in schema["paths"].values() for v in p.values()]) == 48
    assert create_app(settings(ml_enabled=True)).openapi()["paths"]["/api/v1/core/identity/me"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,status",
    [
        ({"active": False}, 401),
        ({"exp": 0}, 401),
        ({"iss": "https://bad.invalid"}, 401),
        ({"aud": ["other"]}, 401),
        ({"client_id": "unknown"}, 401),
        ({"scope": "openid"}, 403),
        ({"scope": ["identity:read"]}, 401),
        ({"client_id": "ai", "sub": "service"}, 403),
    ],
)
async def test_token_type_scope_and_binding_fail_closed(
    core: tuple, changes: dict, status: int
) -> None:
    client, claims, *_ = core
    claims.update(changes)
    response = await client.get(
        "/api/v1/core/identity/me",
        headers={
            "Authorization": "Bearer human-token",
            "X-User-Id": "user-1",
            "X-Role": "ADMIN",
        },
    )
    assert response.status_code == status
    assert "human-token" not in response.text


@pytest.mark.asyncio
async def test_missing_and_duplicate_bearer_rejected(core: tuple) -> None:
    client, *_ = core
    for headers in (
        {"X-User-Id": "admin"},
        [
            ("Authorization", "Bearer human-token"),
            ("Authorization", "Bearer human-token"),
        ],
    ):
        assert (await client.get("/api/v1/core/identity/me", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_revocation_and_department_grants_never_become_global_capabilities(
    core: tuple,
) -> None:
    client, _, records, *_ = core
    record = records.users["human"]
    records.users["human"] = replace(record, grants=())
    headers = {"Authorization": "Bearer human-token"}
    assert (await client.get("/api/v1/core/identity/me", headers=headers)).status_code == 403
    records.users["human"] = replace(record, grants=(Grant("APPROVER", "dept-1"),))
    response = await client.get("/api/v1/core/identity/me", headers=headers)
    assert response.json()["permissions"] == ["identity:read"]
    assert response.json()["roles"] == ["supplier-app:APPROVER@dept-1"]


@pytest.mark.asyncio
async def test_service_requires_token_scope_live_role_and_exact_directory_client(
    core: tuple,
) -> None:
    _, _, records, directory, service, _ = core
    identity = VerifiedIdentity(ISSUER, "service", "ai", "service", frozenset({"identity:read"}))
    actor = await service.resolve(identity, kind="service", permission="identity:read")
    assert actor.account_type == "service"
    for bad in (
        replace(identity, scopes=frozenset()),
        replace(identity, account_type="human"),
        replace(identity, client_id="other"),
    ):
        with pytest.raises(CoreError):
            await service.resolve(bad, kind="service", permission="identity:read")
    directory.client_id = "po"
    with pytest.raises(CoreError):
        await service.resolve(identity, kind="service", permission="identity:read")
    directory.client_id = "ai"
    records.users["service"] = replace(records.users["service"], grants=())
    with pytest.raises(CoreError):
        await service.resolve(identity, kind="service", permission="identity:read")


@pytest.mark.asyncio
async def test_delegation_intersects_rights_and_rejects_invalid_context(core: tuple) -> None:
    _, claims, _, _, service, app = core
    from starlette.requests import Request

    actor = await service.resolve(
        VerifiedIdentity(
            ISSUER,
            "service",
            "ai",
            "service",
            frozenset({"identity:read", "context:delegate", "data:read"}),
        ),
        kind="service",
        permission="context:delegate",
    )

    def request(headers: list[tuple[bytes, bytes]]) -> Request:
        return Request({"type": "http", "headers": headers, "app": app})

    context = await verify_user_context(
        request([(b"x-user-context", b"human-token")]), service, actor, "identity:read"
    )
    assert context.principal.id == "user-1"
    assert context.principal.permissions == ["data:read", "identity:read"]
    for headers in (
        [],
        [(b"x-user-context", b"bad")],
        [(b"x-user-context", b"human-token"), (b"x-user-context", b"human-token")],
        [(b"x-user-context", b"human-token"), (b"x-analysis-job-id", b"job-1")],
    ):
        with pytest.raises(CoreError):
            await verify_user_context(request(headers), service, actor, "identity:read")
    claims["scope"] = "identity:read"
    with pytest.raises(CoreError):
        await verify_user_context(
            request([(b"x-user-context", b"human-token")]), service, actor, "data:read"
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"oidc_allowed_clients": []},
        {"oidc_service_clients": ["web", "ai"]},
        {"oidc_introspection_url": "https://other.invalid/introspect"},
        {"oidc_issuer": None},
        {"core_identity": {**BINDING, "tenant_id": ""}},
        {"core_identity": {**BINDING, "role_permissions": {"ADMIN": ["*"]}}},
    ],
)
def test_incomplete_or_ambiguous_deployment_binding_rejected(updates: dict) -> None:
    with pytest.raises(ValidationError):
        settings(**updates)


@pytest.mark.asyncio
async def test_registered_job_is_dataset_bound_and_rechecks_live_initiator(core: tuple) -> None:
    _, _, records, _, service, _ = core
    actor = await service.resolve(
        VerifiedIdentity(
            ISSUER,
            "service",
            "ai",
            "service",
            frozenset({"data:read"}),
        ),
        kind="service",
        permission="data:read",
    )
    job = RegisteredJob(
        "job-1",
        "tenant-a",
        "supplier-app",
        "ai",
        VerifiedIdentity(ISSUER, "human", "web", "human", frozenset({"data:read"})),
        frozenset({"products"}),
        frozenset({"data:read"}),
        datetime.now(UTC) + timedelta(minutes=5),
        True,
    )

    class Registry:
        current = job

        async def get(self, job_id: str) -> RegisteredJob:
            assert job_id == "job-1"
            return self.current

    registry = Registry()
    result = await resolve_job_context(service, actor, "job-1", "products", registry=registry)
    assert result.principal.permissions == ["data:read:products"]
    assert "workflow:act" not in result.principal.permissions
    with pytest.raises(CoreError):
        await resolve_job_context(service, actor, "job-1", "products")
    with pytest.raises(CoreError):
        await resolve_job_context(service, actor, "job-1", "secrets", registry=registry)
    for changes in (
        {"tenant_id": "other"},
        {"application_id": "other"},
        {"service_client_id": "po"},
        {"active": False},
        {"permissions": frozenset()},
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        {"expires_at": datetime.now()},
    ):
        registry.current = replace(job, **changes)
        with pytest.raises(CoreError):
            await resolve_job_context(service, actor, "job-1", "products", registry=registry)
    registry.current = job
    records.users["human"] = replace(records.users["human"], grants=())
    with pytest.raises(CoreError):
        await resolve_job_context(service, actor, "job-1", "products", registry=registry)


@pytest.mark.asyncio
async def test_database_pool_timeout_returns_sanitized_503(core: tuple) -> None:
    client, _, _, _, _, app = core

    class UnavailablePool:
        async def __aenter__(self) -> None:
            raise PoolTimeoutError("private-connection-metadata")

        async def __aexit__(self, *args: object) -> None:
            pass

    app.dependency_overrides.clear()
    app.state.resources.sessions = UnavailablePool
    response = await client.get(
        "/api/v1/core/identity/me",
        headers={
            "Authorization": "Bearer human-token",
        },
    )
    assert response.status_code == 503
    assert response.json()["code"] == "UPSTREAM_UNAVAILABLE"
    assert "private-connection-metadata" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 504])
async def test_iam_errors_fail_closed_without_disclosing_token(core: tuple, status: int) -> None:
    client, _, _, _, _, app = core

    def failing_iam(request: httpx.Request) -> httpx.Response:
        if status == 504:
            raise httpx.ReadTimeout("private-upstream-error")
        return httpx.Response(500, text="private-upstream-error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing_iam)) as upstream:
        app.state.resources.http = upstream
        response = await client.get(
            "/api/v1/core/identity/me",
            headers={
                "Authorization": "Bearer human-token",
            },
        )
    assert response.status_code == (504 if status == 504 else 503)
    assert "human-token" not in response.text and "private-upstream-error" not in response.text
