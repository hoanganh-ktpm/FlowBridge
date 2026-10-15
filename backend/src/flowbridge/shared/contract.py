# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. You may redistribute and modify it under
# GNU GPL version 3 or later, without any warranty. See LICENSE for full terms.

# Contract chuẩn OpenAPI 0.2.0 và kiểm tra DTO/schema mẫu theo $ref local.

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from flowbridge.shared.errors import CoreError, ErrorCode

CONTRACT_URI = "urn:flowbridge:opencore:0.2.0"
SAMPLE_NOTICE = (
    "SAMPLE API: synthetic data; authentication is simulated with a fixed principal. "
    "Writes return examples without persistence or read-after-write. "
    "No OIDC, database, S3, scanner, BPMN or broker is connected. "
    "Gateway routes are a mapping only; requests go directly to this development backend."
)


@lru_cache(maxsize=1)
def _document() -> dict[str, Any]:
    # Đọc YAML snapshot đóng gói trong package, cache một lần.
    resource = files("flowbridge").joinpath("contracts/opencore-api.yaml")
    return yaml.safe_load(resource.read_text(encoding="utf-8"))


def contract_document() -> dict[str, Any]:
    # Không để schema dành cho docs/sample làm thay đổi contract chuẩn trong cache.
    return deepcopy(_document())


@dataclass(frozen=True, slots=True)
class Operation:
    # Một operation trích từ paths của contract chuẩn.
    method: str
    path: str
    operation_id: str
    status_code: int


def operations() -> tuple[Operation, ...]:
    # Liệt kê method/path/operationId/status 2xx cho route và gateway mapping.
    return tuple(
        Operation(
            method=method.upper(),
            path=path,
            operation_id=spec["operationId"],
            status_code=next(int(s) for s in spec["responses"] if s.startswith("2")),
        )
        for path, methods in _document()["paths"].items()
        for method, spec in methods.items()
        if method in {"get", "post", "patch", "put", "delete"}
    )


def schema_validator(schema: dict[str, Any]) -> Draft202012Validator:
    # Registry chỉ chứa contract local; không tải $ref từ Internet.
    registry = Registry(retrieve=_reject_remote).with_resource(
        CONTRACT_URI, Resource.from_contents(_document(), default_specification=DRAFT202012)
    )
    return Draft202012Validator(schema, registry=registry)


def _reject_remote(uri: str) -> Resource:
    # Chặn $ref ngoài để kiểm tra offline và tránh tải Internet.
    raise ValueError("External schema references are unavailable")


def validate_schema(value: object, schema: dict[str, Any]) -> None:
    # Ném VALIDATION_ERROR khi JSON không khớp schema hợp đồng.
    if not schema_validator(schema).is_valid(value):
        raise CoreError(ErrorCode.VALIDATION_ERROR)


def validate_dto(value: object, name: str) -> None:
    # Kiểm tra object theo tên schema trong components của contract.
    validate_schema(value, {"$ref": f"{CONTRACT_URI}#/components/schemas/{name}"})


def install_sample_openapi(app: FastAPI) -> None:
    # Ghi đè OpenAPI bằng contract mẫu, tắt security production khi sample.
    def sample_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = contract_document()
            schema["info"]["title"] = "FlowBridge OpenCore — Sample API"
            schema["info"]["description"] = SAMPLE_NOTICE
            schema["servers"] = [{"url": "/", "description": "Current development backend"}]
            schema["x-sample-mode"] = True
            schema["x-production-security"] = schema.pop("security", [])
            schema["security"] = []
            for methods in schema["paths"].values():
                for spec in methods.values():
                    spec["x-production-security"] = spec.pop(
                        "security", schema["x-production-security"]
                    )
                    spec["security"] = []
                    spec["description"] = SAMPLE_NOTICE
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = sample_openapi
