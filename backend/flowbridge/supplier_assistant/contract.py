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
from flowbridge.shared.openapi import merge_document, register_fragment

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
    # Runtime extension; keep the versioned upstream snapshots/checksums intact.
    # The router and served OpenAPI consume the same operation definitions.
    for resource, code in (("purchase-orders", "po_number"), ("purchase-requests", "request_code")):
        documents[0]["paths"][f"/api/v1/{resource}"]["get"]["parameters"].append(
            {
                "name": code,
                "in": "query",
                "description": "Exact code, case-insensitive; retains caller read scope.",
                "schema": {"type": "string", "minLength": 1, "maxLength": 255, "pattern": r"\S"},
            }
        )
    documents[0]["paths"]["/api/v1/purchase-orders"]["get"]["parameters"].append(
        {
            "name": "request_id",
            "in": "query",
            "description": "Orders of this exact purchase request; caller read scope applies.",
            "schema": {"type": "string", "format": "uuid"},
        }
    )
    documents[0]["paths"]["/api/v1/purchase-orders"]["get"]["parameters"].append(
        {
            "name": "min_distinct_products",
            "in": "query",
            "description": "Minimum distinct product IDs before pagination; caller scope applies.",
            "schema": {"type": "integer", "minimum": 2, "maximum": 100},
        }
    )
    documents[0]["paths"]["/api/v1/purchase-requests"]["get"]["parameters"].append(
        {
            "name": "min_distinct_suppliers",
            "in": "query",
            "description": "PRs split across suppliers, excluding cancelled POs; page_size <= 10.",
            "schema": {"type": "integer", "minimum": 2, "maximum": 100},
        }
    )
    for document in documents:
        document["components"]["schemas"]["PurchaseOrderPage"]["properties"][
            "min_distinct_products"
        ] = {
            "type": "integer",
            "minimum": 2,
            "maximum": 100,
            "description": "Echo of the applied distinct-product filter, when requested.",
        }
        schemas = document["components"]["schemas"]
        schemas["PurchaseOrderPage"]["properties"]["request_id"] = {
            "type": "string",
            "format": "uuid",
            "description": "Echo of the applied parent-request filter, when requested.",
        }
        schemas["PurchaseRequestPage"]["properties"]["min_distinct_suppliers"] = {
            "type": "integer",
            "minimum": 2,
            "maximum": 100,
        }
        schemas["PurchaseRequest"]["properties"]["purchase_orders"] = {
            "type": "array",
            "maxItems": 100,
            "items": {"$ref": "#/components/schemas/PurchaseOrderRow"},
            "description": "Non-cancelled orders of this request for the supplier filter.",
        }
        schemas["PurchaseRequest"]["properties"]["distinct_supplier_count"] = {
            "type": "integer",
            "minimum": 2,
        }
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
    register_fragment(
        app,
        "database",
        lambda: database_document(
            rag_postgres_enabled=app.state.settings.rag_postgres_enabled,
            chat_enabled=app.state.settings.chat_enabled,
        ),
        base=True,
    )


def database_document(
    *, rag_postgres_enabled: bool = False, chat_enabled: bool = False
) -> dict[str, Any]:
    application, tools = application_documents()
    merge_document(application, tools)
    application["info"]["title"] = "FlowBridge Supplier Assistant — PostgreSQL API"
    # Resolve against the served OpenAPI URL, including a proxy prefix or custom port.
    # The published contract's example host must not direct live Swagger requests.
    application["servers"] = [{"url": "."}]
    application["info"]["description"] = (
        "Persistent PostgreSQL business API. Bearer tokens require configured IdP "
        "introspection; database roles enforce resource access. Identity provisioning "
        "links an existing verified Keycloak user through the configured directory. "
        "Workflow approval, chat and AI/document engines return 503 until connected. "
        "Outbox events remain pending until a durable worker processes them."
    )
    if rag_postgres_enabled:
        application["info"]["description"] += (
            " PostgreSQL knowledge retrieval/indexing is enabled: exact cosine, BM25/RRF, "
            "verified AI_SERVICE and database-backed X-Job-Context. UserContext-only "
            "knowledge calls are not supported by this implementation."
        )
    if chat_enabled:
        application["info"]["description"] += (
            " Chat uses the configured Langflow caller bridge; its operational projection "
            "requires migration 0005_chat_projection. RAG in chat requires an authorized "
            "analysis job and real AI service credentials. Native chat memory is disabled."
        )
        application["paths"]["/api/v1/chat/messages"]["post"]["description"] = (
            "Execute one Langflow turn as a verified human business reader "
            "(EMPLOYEE, APPROVER, WAREHOUSE or PROCUREMENT_VIEWER). Session ownership and "
            "request access are checked before execution and on replay. Idempotency-Key "
            "is reserved durably before execution; interrupted/failed turns never auto-retry. "
            "Chat history lives in operational projection tables outside the supplier baseline."
        )
        history = application["paths"]["/api/v1/chat/sessions/{session_id}/messages"]["get"]
        history["summary"] = "Lịch sử chat thuộc caller"
        history["description"] = (
            "Read completed turns after checking an active human business reader, "
            "session ownership, bound "
            "request and observed resource/source permissions. No Langflow history is exposed."
        )
    application["x-database-migration"] = (
        "0005_chat_projection"
        if chat_enabled
        else "0003_core_knowledge"
        if rag_postgres_enabled
        else "0002_api_idempotency"
    )
    return application


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
