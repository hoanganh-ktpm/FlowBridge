# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# DTO JSON Schema đọc từ snapshot có version; runtime không phụ thuộc PDF hoặc workspace cha.

from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from flowbridge.shared.errors import CoreError, ErrorCode

CONTRACT_URI = "urn:flowbridge:supplier-assistant:0.4.1"
SAMPLE_NOTICE = (
    "SAMPLE v0.4.1: synthetic fixtures without persistence. No database, IdP, workflow, "
    "email, document store or TabICLv2 model is connected. Authentication and role actors "
    "are simulated server-side per operation; client headers never select a role. "
    "Writes return examples and do not change subsequent GET responses."
)


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
    # Trả bản sao để Swagger sample không sửa contract production trong cache.
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


def merge_sample_openapi(schema: dict[str, Any]) -> None:
    # Namespace DTO ứng dụng để không ghi đè Id/Principal/Notification của core.
    def namespace(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "#/components/schemas/SA" + item.rsplit("/", 1)[1]
                if key == "$ref" and item.startswith("#/components/schemas/")
                else namespace(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [namespace(item) for item in value]
        return value

    for document in _documents():
        for name, dto in document["components"]["schemas"].items():
            schema["components"]["schemas"]["SA" + name] = namespace(dto)
        for name, scheme in document["components"]["securitySchemes"].items():
            schema["components"]["securitySchemes"]["SA" + name] = deepcopy(scheme)
        for path, entry in document["paths"].items():
            if path in schema["paths"]:
                raise RuntimeError("Application path collides with Open-Core")
            rewritten = namespace(entry)
            for spec in rewritten.values():
                spec["x-production-security"] = [
                    {"SA" + key: scopes for key, scopes in requirement.items()}
                    for requirement in spec.get("security", document["security"])
                ]
                spec["security"] = []
                spec["description"] = SAMPLE_NOTICE + "\n\n" + spec["description"]
                spec["x-sample-role"] = spec["x-required-role"]
            schema["paths"][path] = rewritten
    schema["info"]["title"] = "FlowBridge — OpenCore and Supplier Assistant Sample API"
    schema["info"]["description"] += "\n\n" + SAMPLE_NOTICE
    schema["x-application-schema-version"] = "0.4.1"
