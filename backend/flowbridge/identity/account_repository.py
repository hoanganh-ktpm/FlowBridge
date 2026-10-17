# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Identity owns this SQL adapter; all writes use the caller's transaction.
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from flowbridge.models import Department, Role, User, UserRole
from flowbridge.shared.errors import CoreError, ErrorCode


class IdentityAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_identity(self, issuer: str, subject: str) -> None:
        await self.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": "identity:" + issuer + ":" + subject},
        )

    async def active_admins(self) -> list[UUID]:
        await self.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext('flowbridge.identity.bootstrap'))")
        )
        users, grants, roles = User.__table__, UserRole.__table__, Role.__table__
        result = await self.session.execute(
            sa.select(users.c.user_id)
            .join(grants, grants.c.user_id == users.c.user_id)
            .join(roles, roles.c.role_id == grants.c.role_id)
            .where(
                users.c.status == "active",
                users.c.account_type == "human",
                roles.c.role_code == "ADMIN",
                roles.c.status == "active",
                grants.c.revoked_at.is_(None),
                grants.c.scope_type == "global",
                grants.c.department_id.is_(None),
            )
        )
        return list(result.scalars())

    async def find_identity(self, issuer: str, subject: str) -> dict[str, Any] | None:
        users = User.__table__
        result = await self.session.execute(
            sa.select(users)
            .where(users.c.external_issuer == issuer, users.c.external_subject == subject)
            .with_for_update()
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def department(self, identifier: UUID) -> dict[str, Any]:
        target = Department.__table__
        return await self._one(sa.select(target).where(target.c.department_id == identifier))

    async def role(self, code: str) -> dict[str, Any]:
        target = Role.__table__
        return await self._one(sa.select(target).where(target.c.role_code == code))

    async def active_grant(
        self, user_id: UUID, role_id: UUID, department_id: UUID | None
    ) -> dict[str, Any] | None:
        target = UserRole.__table__
        result = await self.session.execute(
            sa.select(target)
            .where(
                target.c.user_id == user_id,
                target.c.role_id == role_id,
                target.c.revoked_at.is_(None),
                target.c.department_id == department_id,
            )
            .order_by(target.c.user_role_id)
            .with_for_update()
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def insert_user(self, values: dict[str, Any]) -> dict[str, Any]:
        return await self._one(sa.insert(User.__table__).values(**values).returning(User.__table__))

    async def insert_grant(self, values: dict[str, Any]) -> dict[str, Any]:
        target = UserRole.__table__
        return await self._one(sa.insert(target).values(**values).returning(target))

    async def _one(self, statement: Any) -> dict[str, Any]:
        row = (await self.session.execute(statement)).mappings().first()
        if row is None:
            raise CoreError(ErrorCode.NOT_FOUND)
        return dict(row)
