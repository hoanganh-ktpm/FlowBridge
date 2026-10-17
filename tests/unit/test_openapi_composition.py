# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from copy import deepcopy

import pytest
from fastapi import FastAPI

from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.shared.openapi import merge_document, register_fragment


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
