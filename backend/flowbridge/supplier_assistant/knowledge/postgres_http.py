# SPDX-License-Identifier: GPL-3.0-or-later
"""Verified AI identity + database-backed JobContext, never roles from request JSON."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.contract import validate_dto
from flowbridge.supplier_assistant.policy import SupplierPolicy, require
from flowbridge.supplier_assistant.repository import SupplierRepository, table

from .models import KnowledgeError, RetrievalRequest
from .postgres_domain import PgScope


async def job_scope(
    sessions, identity: VerifiedIdentity, task_id: UUID, supplier_id=None
) -> PgScope:
    async with sessions() as session:
        repository = SupplierRepository(session)
        policy = SupplierPolicy(repository)
        await policy.authenticate(identity)
        policy.authorize("AI_SERVICE")
        task = await repository.get("analysis_tasks", task_id)
        request = await policy.request(task["request_id"])
        require(task["status"] in {"queued", "running", "succeeded"})
        require(request["status"] != "cancelled", ErrorCode.REQUEST_CHANGED)
        require(task["input_revision"] == request["input_revision"], ErrorCode.REQUEST_CHANGED)
        require(task["feature_asof"] <= datetime.now(UTC), ErrorCode.REQUEST_CHANGED)
        require(
            bool(
                await repository.rows(
                    "departments",
                    table("departments").c.department_id == request["department_id"],
                    table("departments").c.status == "active",
                )
            ),
            ErrorCode.FORBIDDEN,
        )
        return PgScope(task["feature_asof"], request["department_id"], supplier_id)


async def handle_postgres_knowledge(request, identity, operation: str, payload: dict) -> dict:
    require(identity is not None, ErrorCode.UNAUTHENTICATED)
    # X-Job-Context is an opaque task UUID looked up under the authenticated service's
    # AI_SERVICE grant. A department/user/cutoff embedded by the caller is never trusted.
    require(not request.headers.getlist("X-User-Context"), ErrorCode.VALIDATION_ERROR)
    contexts = request.headers.getlist("X-Job-Context")
    require(len(contexts) == 1, ErrorCode.VALIDATION_ERROR)
    try:
        task_id = UUID(contexts[0])
    except (ValueError, TypeError):
        raise CoreError(ErrorCode.VALIDATION_ERROR) from None
    resources = request.app.state.resources
    service = request.app.state.rag_postgres_service
    require(resources is not None and service is not None, ErrorCode.UPSTREAM_UNAVAILABLE)

    async def scope():
        return await job_scope(
            resources.sessions,
            identity,
            task_id,
            UUID(payload["supplier_id"]) if payload.get("supplier_id") else None,
        )

    await scope()
    namespace = (
        "knowledge-index:"
        + hashlib.sha256(f"{identity.issuer}|{identity.subject}|{task_id}".encode()).hexdigest()
    )
    key = request.headers["Idempotency-Key"]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    async def key_guard(result=None):
        async with resources.sessions() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": namespace + key},
            )
            old = (
                await session.execute(
                    text(
                        "SELECT request_hash FROM public.api_idempotency "
                        "WHERE scope=:scope AND key=:key"
                    ),
                    {"scope": namespace, "key": key},
                )
            ).scalar_one_or_none()
            require(old is None or old == digest, ErrorCode.IDEMPOTENCY_CONFLICT)
            if old is None and result is not None:
                await session.execute(
                    text(
                        "INSERT INTO public.api_idempotency"
                        "(scope,key,request_hash,response,created_at) "
                        "VALUES(:scope,:key,:hash,CAST(:response AS jsonb),now())"
                    ),
                    {
                        "scope": namespace,
                        "key": key,
                        "hash": digest,
                        "response": json.dumps(result),
                    },
                )

    try:
        if operation == "retrieveSupplierKnowledge":
            result = await service.search(RetrievalRequest.model_validate(payload), scope)
            validate_dto(result, "RetrievalResponse")
        else:
            # The index operation is synchronous (200), as specified by the existing contract.
            await key_guard()
            result = await service.index_references(payload["documents"], scope)
            await key_guard(result)
            validate_dto(result, "KnowledgeIndexResult")
        return result
    except ValidationError:
        raise CoreError(ErrorCode.VALIDATION_ERROR) from None
    except KnowledgeError as exc:
        code = (
            ErrorCode.MODEL_UNAVAILABLE
            if str(exc).startswith("MODEL_")
            else (ErrorCode.UPSTREAM_UNAVAILABLE)
        )
        raise CoreError(code) from None
