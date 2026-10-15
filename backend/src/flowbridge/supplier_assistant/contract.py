# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# DTO JSON Schema đọc từ snapshot có version; runtime không phụ thuộc PDF hoặc workspace cha.

from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml
from fastapi import FastAPI
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from flowbridge.shared.errors import CoreError, ErrorCode

CONTRACT_URI = "urn:flowbridge:supplier-assistant:0.4.1"


@lru_cache(maxsize=1)
def _documents() -> tuple[dict[str, Any], ...]:
    # App và tools có cùng DTO; schema khác nhau là lỗi đóng gói.
    root = files("flowbridge").joinpath("contracts")
    documents = tuple(
        yaml.safe_load(root.joinpath(name).read_text(encoding="utf-8"))
        for name in ("app-api.yaml", "ai-tools-api.yaml")
    )
    if documents[0]["components"]["schemas"] != documents[1]["components"]["schemas"]:
        raise RuntimeError("Application and AI contract schemas differ")
    return documents


def application_documents() -> tuple[dict[str, Any], ...]:
    # Trả bản sao để Swagger không sửa contract chuẩn trong cache.
    return deepcopy(_documents())


def application_operations() -> tuple[tuple[str, str, dict[str, Any]], ...]:
    # Dùng chung registry cho router, examples và kiểm tra coverage.
    return tuple(
        (method.upper(), path, deepcopy(spec))
        for document in _documents()
        for path, entry in document["paths"].items()
        for method, spec in entry.items()
        if method in {"get", "post", "put", "patch", "delete"}
    )


def install_database_openapi(app: FastAPI) -> None:
    def database_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            application, tools = application_documents()
            application["paths"].update(tools["paths"])
            application["info"]["title"] = "FlowBridge Supplier Assistant — PostgreSQL API"
            application["info"]["description"] = (
                "Persistent PostgreSQL business API. Bearer tokens require configured IdP "
                "introspection; database roles enforce resource access. Workflow approval, "
                "identity provisioning, chat and AI/document engines return 503 until connected. "
                "Outbox events remain pending until a durable worker processes them."
            )
            application["x-database-migration"] = "0002_api_idempotency"
            app.openapi_schema = application
        return app.openapi_schema

    app.openapi = database_openapi


@lru_cache(maxsize=256)
def dto_validator(name: str) -> Draft202012Validator:
    # Registry chỉ local, không cho contract tải remote $ref.
    registry = Registry().with_resource(
        CONTRACT_URI, Resource.from_contents(_documents()[0], default_specification=DRAFT202012)
    )
    return Draft202012Validator(
        {"$ref": f"{CONTRACT_URI}#/components/schemas/{name}"},
        registry=registry,
        format_checker=FormatChecker(),
    )


def validate_dto(value: object, name: str) -> None:
    # Lỗi công khai không chứa input, token hoặc exception validator.
    if not dto_validator(name).is_valid(value):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
