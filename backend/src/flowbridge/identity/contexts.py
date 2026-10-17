# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Job authorization boundary. No job header is authoritative without a trusted registry adapter.
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.identity.service import IdentityService, PrincipalContext
from flowbridge.shared.errors import CoreError, ErrorCode


@dataclass(frozen=True, slots=True)
class RegisteredJob:
    id: str
    tenant_id: str
    application_id: str
    service_client_id: str
    initiator: VerifiedIdentity
    datasets: frozenset[str]
    permissions: frozenset[str]
    expires_at: datetime
    active: bool


class JobRegistry(Protocol):
    async def get(self, job_id: str) -> RegisteredJob | None: ...


async def resolve_job_context(
    service: IdentityService,
    actor: PrincipalContext,
    job_id: str,
    dataset: str,
    *,
    registry: JobRegistry | None = None,
) -> PrincipalContext:
    actor.require("data:read")
    if actor.account_type != "service" or not job_id or not dataset:
        raise CoreError(ErrorCode.FORBIDDEN)
    if registry is None:
        # Core job registry belongs to ingestion/workers; never use app job JSON as a fallback.
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    job = await registry.get(job_id)
    if (
        job is None
        or job.id != job_id
        or not job.active
        or job.tenant_id != actor.principal.tenant_id
        or job.application_id != actor.application_id
        or job.service_client_id != actor.client_id
        or dataset not in job.datasets
        or "data:read" not in job.permissions
        or job.expires_at.tzinfo is None
        or job.expires_at <= datetime.now(UTC)
        or job.initiator.account_type != "human"
    ):
        raise CoreError(ErrorCode.FORBIDDEN)
    user = await service.resolve(job.initiator, kind="human", permission="data:read")
    if user.principal.tenant_id != job.tenant_id or user.application_id != job.application_id:
        raise CoreError(ErrorCode.FORBIDDEN)
    # Only this dataset's read capability; never usable for a workflow action or arbitrary datasets.
    return PrincipalContext(
        user.principal.model_copy(update={"permissions": [f"data:read:{dataset}"]}),
        user.application_id,
        "human",
        user.client_id,
    )
