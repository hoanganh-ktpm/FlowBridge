# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from copy import deepcopy
from urllib.parse import urljoin

import pytest
from fastapi import FastAPI

from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.shared.openapi import merge_document, register_fragment
from flowbridge.supplier_assistant.contract import application_documents


@pytest.mark.parametrize("collision", ["path", "operation", "schema", "security"])
def test_composer_rejects_collisions(collision: str) -> None:
    base = {
        "paths": {"/a": {"get": {"operationId": "read"}}},
        "components": {
            "schemas": {"Record": {"type": "string"}},
            "securitySchemes": {"Bearer": {"type": "http", "scheme": "bearer"}},
        },
    }
    fragment = {
        "path": {"paths": deepcopy(base["paths"])},
        "operation": {"paths": {"/b": {"get": {"operationId": "read"}}}},
        "schema": {"components": {"schemas": {"Record": {"type": "integer"}}}},
        "security": {"components": {"securitySchemes": {"Bearer": {"type": "apiKey"}}}},
    }[collision]
    with pytest.raises(ValueError, match="collision"):
        merge_document(base, fragment)


def test_composer_cache_invalidates_and_isolates_apps() -> None:
    app, other = FastAPI(), FastAPI()
    fragment = {"paths": {"/a": {"get": {"operationId": "read"}}}}
    register_fragment(app, "a", lambda: fragment)
    first = app.openapi()
    assert app.openapi() is first
    register_fragment(app, "b", lambda: {"paths": {"/b": {"get": {"operationId": "second"}}}})
    assert set(app.openapi()["paths"]) == {"/a", "/b"}
    assert set(first["paths"]) == {"/a"}
    assert fragment == {"paths": {"/a": {"get": {"operationId": "read"}}}}
    assert other.openapi()["paths"] == {}


@pytest.mark.parametrize("ml,count", [(False, 46), (True, 51)])
def test_database_profiles_match_live_route_registry(ml: bool, count: int) -> None:
    app = create_app(
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://x:y@localhost/test",
            ml_enabled=ml,
        )
    )
    exported = app.openapi()
    app.openapi_schema = None
    live = FastAPI.openapi(app)

    def operations(schema: dict) -> set[tuple[str, str, str]]:
        return {
            (path, method, spec["operationId"])
            for path, entry in schema["paths"].items()
            for method, spec in entry.items()
            if method in {"get", "post", "patch", "put", "delete"}
        }

    assert operations(live) == operations(exported)
    assert len(operations(exported)) == count


@pytest.mark.parametrize(
    "schema_url,expected",
    [
        ("http://127.0.0.1:18082/openapi.json", "http://127.0.0.1:18082/api/v1/departments"),
        ("http://localhost:19000/openapi.json", "http://localhost:19000/api/v1/departments"),
        (
            "https://example.test/backend/openapi.json",
            "https://example.test/backend/api/v1/departments",
        ),
    ],
)
def test_swagger_uses_serving_origin_without_changing_contract(
    schema_url: str, expected: str
) -> None:
    original = application_documents()
    app = create_app(
        Settings(_env_file=None, database_url="postgresql+asyncpg://x:y@localhost/test")
    )
    schema = app.openapi()
    assert schema["servers"] == [{"url": "."}]
    base = urljoin(schema_url, schema["servers"][0]["url"])
    assert urljoin(base, "api/v1/departments") == expected
    assert application_documents() == original
    assert schema["components"]["securitySchemes"] == original[0]["components"]["securitySchemes"]


@pytest.mark.parametrize("core", [False, True])
@pytest.mark.parametrize("ml", [False, True])
def test_postgres_rag_preserves_identity_and_ml_fragments(core: bool, ml: bool) -> None:
    issuer = "https://iam.example.invalid/realms/merge"
    app = create_app(
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://x:y@localhost/test",
            rag_postgres_enabled=True,
            ml_enabled=ml,
            oidc_issuer=issuer,
            oidc_audience="core",
            oidc_client_id="api",
            oidc_client_secret="test-only-secret",
            oidc_introspection_url=issuer + "/protocol/openid-connect/token/introspect",
            oidc_allowed_clients=["web", "mobile", "ai"],
            oidc_service_clients=["ai"],
            core_identity={
                "tenant_id": "tenant-a",
                "application_id": "supplier-app",
                "web_client_id": "web",
                "mobile_client_id": "mobile",
                "service_clients": {"ai": {"role": "AI_SERVICE"}},
            }
            if core
            else None,
        )
    )
    exported = app.openapi()
    operation = exported["paths"]["/internal/v1/tools/retrieve-knowledge"]["post"]
    assert exported["x-database-migration"] == "0003_core_knowledge"
    assert "PostgreSQL knowledge retrieval/indexing is enabled" in exported["info"]["description"]
    assert operation["security"] == [{"ServiceAuth": [], "JobContext": []}]
    assert any(p["name"] == "X-Job-Context" and p["required"] for p in operation["parameters"])
    identity_path = "/api/v1/core/identity/me"
    assert (identity_path in exported["paths"]) is core
    if core:
        assert exported["paths"][identity_path]["get"]["security"] == [{"BearerAuth": []}]

    def operations(schema: dict) -> set[tuple[str, str, str]]:
        return {
            (path, method, spec["operationId"])
            for path, entry in schema["paths"].items()
            for method, spec in entry.items()
            if method in {"get", "post", "patch", "put", "delete"}
        }

    app.openapi_schema = None
    assert operations(FastAPI.openapi(app)) == operations(exported)
    assert len(operations(exported)) == 46 + 5 * ml + 2 * core
    other = create_app(
        Settings(_env_file=None, database_url="postgresql+asyncpg://x:y@localhost/test")
    )
    assert other.openapi()["x-database-migration"] == "0002_api_idempotency"
