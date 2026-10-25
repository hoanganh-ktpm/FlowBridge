# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
from typing import Literal
from uuid import UUID

from fastapi import Header, HTTPException
from pydantic import Field, field_validator, model_validator

from .decision_repository import DecisionRepository
from .decision_service import DecisionPolicy, DecisionService
from .schemas import DTO


class CreateDecision(DTO):
    simulation_run_id: UUID
    order_id: UUID
    title: str = Field(min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError("A descriptive title is required")
        return value.strip()


class ReviseDecision(DTO):
    simulation_run_id: UUID
    expected_version: int = Field(ge=1, strict=True)


class SubmitDecision(DTO):
    revision_id: UUID
    expected_version: int = Field(ge=1, strict=True)


class VerifyHandoff(DTO):
    revision_id: UUID


class CompleteDecisionTask(DTO):
    revision_id: UUID
    outcome: Literal["confirmed", "changes_required", "approved", "rejected", "acknowledged"]
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def return_reason(self):
        if self.outcome in {"changes_required", "rejected"} and not (self.note or "").strip():
            raise ValueError("Cần ghi lý do trả lại hoặc không chấp thuận phương án")
        return self


def install_decision_routes(
    app,
    prefix,
    actor_dependency,
    repository,
    reauthorize,
    case_repository=None,
    decision_bindings=None,
    inspector=None,
    workflow=None,
):
    policy = DecisionPolicy(
        decision_bindings
        if decision_bindings is not None
        else json.loads(os.environ.get("FLOWBRIDGE_WORKSPACE_DECISION_BINDINGS", "{}"))
    )
    if case_repository is None and hasattr(repository, "engine"):
        case_repository = DecisionRepository(repository.engine)
    service = DecisionService(case_repository, repository, policy, reauthorize, inspector, workflow)

    def configured():
        if service.repository is None or not policy.bindings:
            raise HTTPException(
                503, "Decision dossiers require configured operational assignments and migrations"
            )

    @app.get(prefix + "/decision-capabilities")
    async def capabilities(ctx=actor_dependency):
        binding = policy.bindings.get(str(ctx.owner_id))
        enabled = (
            service.repository is not None and binding is not None and binding.org_id == ctx.org_id
        )
        return {
            "enabled": enabled,
            "roles": binding.roles if enabled else [],
            "warehouse_ids": [str(value) for value in binding.warehouse_ids] if enabled else [],
            "workflow_enabled": workflow is not None and inspector is not None,
        }

    @app.post(prefix + "/decisions", status_code=201)
    async def create(
        body: CreateDecision, ctx=actor_dependency, idempotency_key: str = Header(default="")
    ):
        configured()
        return await service.create(ctx, body, idempotency_key)

    @app.get(prefix + "/decisions")
    async def listing(ctx=actor_dependency):
        configured()
        return await service.list(ctx)

    @app.get(prefix + "/decisions/{case_id}")
    async def detail(case_id: UUID, ctx=actor_dependency):
        configured()
        return await service.get(ctx, case_id)

    @app.post(prefix + "/decisions/{case_id}/revisions", status_code=201)
    async def revise(
        case_id: UUID,
        body: ReviseDecision,
        ctx=actor_dependency,
        idempotency_key: str = Header(default=""),
    ):
        configured()
        return await service.revise(ctx, case_id, body, idempotency_key)

    @app.get(prefix + "/decisions/{case_id}/revisions")
    async def history(case_id: UUID, ctx=actor_dependency):
        configured()
        return await service.history(ctx, case_id)

    @app.get(prefix + "/decisions/{case_id}/timeline")
    async def timeline(case_id: UUID, ctx=actor_dependency):
        configured()
        return await service.timeline(ctx, case_id)

    @app.post(prefix + "/decisions/{case_id}/submit")
    async def submit(
        case_id: UUID,
        body: SubmitDecision,
        ctx=actor_dependency,
        idempotency_key: str = Header(default=""),
    ):
        configured()
        from .decision_repository import command_key

        command_key(idempotency_key)
        return await service.submit(ctx, case_id, body, idempotency_key)

    @app.get(prefix + "/decisions/{case_id}/workflow")
    async def state(case_id: UUID, ctx=actor_dependency):
        configured()
        return await service.workflow_state(ctx, case_id)

    @app.post(prefix + "/decisions/{case_id}/verify-handoff")
    async def verify(
        case_id: UUID,
        body: VerifyHandoff,
        ctx=actor_dependency,
        idempotency_key: str = Header(default=""),
    ):
        configured()
        from .decision_repository import command_key

        command_key(idempotency_key)
        return await service.verify_handoff(ctx, case_id, body, idempotency_key)

    @app.post(prefix + "/decisions/{case_id}/tasks/{task_id}/complete")
    async def complete(
        case_id: UUID,
        task_id: str,
        body: CompleteDecisionTask,
        ctx=actor_dependency,
        idempotency_key: str = Header(default=""),
    ):
        configured()
        import re

        from .decision_repository import command_key

        command_key(idempotency_key)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", task_id):
            raise HTTPException(422, "Invalid workflow task identifier")
        return await service.complete_task(ctx, case_id, task_id, body, idempotency_key)
