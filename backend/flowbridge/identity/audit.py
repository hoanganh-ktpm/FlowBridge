# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Transactional Identity audit/event sink, using the existing outbox schema.
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from flowbridge.models import OutboxEvent


class IdentityAuditSink(Protocol):
    async def bootstrap_applied(self, user_id: UUID, actor_id: UUID, roles: list[str]) -> None: ...


class IdentityOutbox:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bootstrap_applied(self, user_id: UUID, actor_id: UUID, roles: list[str]) -> None:
        event_id, timestamp = uuid4(), datetime.now(UTC)
        await self.session.execute(
            sa.insert(OutboxEvent.__table__).values(
                event_id=event_id,
                aggregate_type="user",
                aggregate_id=user_id,
                event_type="identity.bootstrap_applied",
                payload={"user_id": str(user_id), "actor_id": str(actor_id), "roles": roles},
                idempotency_key=str(event_id),
                status="pending",
                attempt_count=0,
                available_at=timestamp,
                published_at=None,
                last_error=None,
                created_at=timestamp,
            )
        )
