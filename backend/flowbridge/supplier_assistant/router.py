# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Biên HTTP dùng DTO JSON Schema cùng contract, không duy trì DTO Python thứ hai bị lệch.

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flowbridge.db import get_session
from flowbridge.identity.authentication import VerifiedIdentity, authenticated_identity
from flowbridge.identity.directory import KeycloakDirectory
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.contract import (
    application_operations,
    validate_dto,
)
from flowbridge.supplier_assistant.database_service import SupplierDatabaseService
from flowbridge.supplier_assistant.repository import SupplierRepository


async def parse_request(
    request: Request,
    spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    query: dict[str, Any] = {}
    for parameter in spec.get("parameters", []):
        name, location = parameter["name"], parameter["in"]
        value: Any = None
        if location == "path":
            value = request.path_params.get(name)
        elif location == "header":
            values = request.headers.getlist(name)
            if len(values) > 1:
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            value = values[0] if values else None
        elif location == "query":
            values = request.query_params.getlist(name)
            if len(values) > 1:
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            value = values[0] if values else parameter["schema"].get("default")
            if parameter["schema"].get("type") == "integer" and value is not None:
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    raise CoreError(ErrorCode.VALIDATION_ERROR) from None
        if value is None:
            if parameter.get("required"):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            continue
        schema = parameter["schema"]
        if "$ref" in schema:
            validate_dto(value, schema["$ref"].rsplit("/", 1)[1])
        elif not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(value):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if location == "query":
            query[name] = value
    payload: dict[str, Any] = {}
    if "requestBody" in spec:
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="Malformed JSON") from None
        body_schema = spec["requestBody"]["content"]["application/json"]["schema"]
        validate_dto(payload, body_schema["$ref"].rsplit("/", 1)[1])
    return payload, query


async def supplier_token_identity() -> None:
    # Public supplier routes use a database-backed, order-bound confirmation token instead.
    return None


def database_endpoint(
    spec: dict[str, Any], method: str
) -> Callable[..., Awaitable[dict[str, Any]]]:
    identity_dependency = (
        supplier_token_identity
        if spec["x-required-role"] == "SUPPLIER_TOKEN"
        else authenticated_identity
    )

    async def endpoint(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        identity: Annotated[VerifiedIdentity | None, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        payload, query = await parse_request(request, spec)
        success = next(code for code in spec["responses"] if code.startswith("2"))
        schema = spec["responses"][success]["content"]["application/json"]["schema"]
        if spec["operationId"] in {"sendChatMessage", "getChatHistory"}:
            from flowbridge.supplier_assistant.chat import handle_chat

            try:
                return await handle_chat(request, identity, spec["operationId"], payload, query)
            except DBAPIError:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        if request.app.state.settings.rag_postgres_enabled and spec["operationId"] in {
            "retrieveSupplierKnowledge",
            "indexPreparedMarkdown",
        }:
            from flowbridge.supplier_assistant.knowledge.postgres_http import (
                handle_postgres_knowledge,
            )

            try:
                async with asyncio.timeout(
                    request.app.state.settings.database_command_timeout_seconds
                ):
                    return await handle_postgres_knowledge(
                        request, identity, spec["operationId"], payload
                    )
            except TimeoutError:
                raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
            except DBAPIError:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        resources = request.app.state.resources
        service = SupplierDatabaseService(
            SupplierRepository(session),
            directory=KeycloakDirectory(resources.http, request.app.state.settings)
            if resources is not None
            else None,
        )
        try:
            async with asyncio.timeout(request.app.state.settings.database_command_timeout_seconds):
                async with session.begin():
                    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    return await service.execute(
                        spec["operationId"],
                        spec["x-required-role"],
                        payload,
                        dict(request.path_params),
                        query,
                        schema["$ref"].rsplit("/", 1)[1],
                        identity,
                        request.headers.get("Idempotency-Key"),
                        request.headers.get("X-Confirmation-Token"),
                        read=method == "GET",
                    )
        except IntegrityError:
            raise CoreError(ErrorCode.INVALID_STATE) from None
        except TimeoutError:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        except DBAPIError:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None

    return endpoint


def create_database_router() -> APIRouter:
    router = APIRouter()
    for method, path, spec in application_operations():
        status = int(next(code for code in spec["responses"] if code.startswith("2")))
        router.add_api_route(
            path,
            database_endpoint(spec, method),
            methods=[method],
            operation_id=spec["operationId"],
            status_code=status,
            response_model=None,
            tags=spec["tags"],
        )
    return router
