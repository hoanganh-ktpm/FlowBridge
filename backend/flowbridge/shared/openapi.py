# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# One composer owns caching and rejects ambiguous module contributions.
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

Document = dict[str, Any]
METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def merge_document(target: Document, fragment: Document) -> None:
    for path, entry in fragment.get("paths", {}).items():
        current = target.setdefault("paths", {}).setdefault(path, {})
        for key, value in entry.items():
            if key in current and (key in METHODS or current[key] != value):
                raise ValueError(f"OpenAPI path collision: {path} {key}")
            current[key] = deepcopy(value)
    for section, entries in fragment.get("components", {}).items():
        current = target.setdefault("components", {}).setdefault(section, {})
        for name, value in entries.items():
            if name in current and current[name] != value:
                raise ValueError(f"OpenAPI component collision: {section}/{name}")
            current[name] = deepcopy(value)
    for key, value in fragment.items():
        if key.startswith("x-"):
            if key in target and target[key] != value:
                raise ValueError(f"OpenAPI extension collision: {key}")
            target[key] = deepcopy(value)
    operation_ids: set[str] = set()
    for entry in target.get("paths", {}).values():
        for method, spec in entry.items():
            if method in METHODS:
                identifier = spec.get("operationId")
                if not identifier or identifier in operation_ids:
                    raise ValueError(f"OpenAPI operationId collision or missing: {identifier}")
                operation_ids.add(identifier)


def namespace_schemas(document: Document, prefix: str) -> Document:
    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "#/components/schemas/" + prefix + item.rsplit("/", 1)[1]
                if key == "$ref"
                and isinstance(item, str)
                and item.startswith("#/components/schemas/")
                else rewrite(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    result = rewrite(document)
    components = result.get("components", {})
    if "schemas" in components:
        components["schemas"] = {
            prefix + key: value for key, value in components["schemas"].items()
        }
    return result


def install_openapi(app: FastAPI) -> None:
    if getattr(app.state, "openapi_fragments", None) is not None:
        return
    app.state.openapi_fragments = {}
    app.state.openapi_base = None

    def composed_openapi() -> Document:
        if app.openapi_schema is None:
            base = app.state.openapi_base
            fragments = app.state.openapi_fragments
            schema = (
                deepcopy(base())
                if base
                else get_openapi(
                    title=app.title, version=app.version, routes=[] if fragments else app.routes
                )
            )
            for factory in fragments.values():
                merge_document(schema, factory())
            # Validate the base as well, even when there are no fragments.
            merge_document(schema, {})
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = composed_openapi


def register_fragment(
    app: FastAPI, name: str, factory: Callable[[], Document], *, base: bool = False
) -> None:
    install_openapi(app)
    if name in app.state.openapi_fragments or (base and app.state.openapi_base is not None):
        raise ValueError(f"OpenAPI module already registered: {name}")
    if base:
        app.state.openapi_base = factory
    else:
        app.state.openapi_fragments[name] = factory
    app.openapi_schema = None
