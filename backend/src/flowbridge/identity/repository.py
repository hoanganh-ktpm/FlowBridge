# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Adapter to the existing identity/grant schema; no dependency on application repositories.
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from flowbridge.models import Department, Role, User, UserRole
from flowbridge.shared.errors import CoreError, ErrorCode


@dataclass(frozen=True, slots=True)
class Grant:
    role: str
    department_id: str | None


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    id: str
    display_name: str
    account_type: Literal["human", "service"]
    grants: tuple[Grant, ...]


class IdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(self, issuer: str, subject: str) -> IdentityRecord:
        users, roles = User.__table__, Role.__table__
        grants, departments = UserRole.__table__, Department.__table__
        # One statement observes identity and current grants in the same DB snapshot.
        active_grants = (
            sa.select(
                grants.c.user_id,
                roles.c.role_code,
                grants.c.department_id,
                roles.c.allowed_account_type,
            )
            .join(
                roles,
                sa.and_(
                    roles.c.role_id == grants.c.role_id,
                    roles.c.scope_type == grants.c.scope_type,
                ),
            )
            .outerjoin(departments, departments.c.department_id == grants.c.department_id)
            .where(
                grants.c.revoked_at.is_(None),
                roles.c.status == "active",
                sa.or_(
                    sa.and_(grants.c.scope_type == "global", grants.c.department_id.is_(None)),
                    sa.and_(
                        grants.c.scope_type == "department",
                        grants.c.department_id.is_not(None),
                        departments.c.status == "active",
                    ),
                ),
            )
            .subquery()
        )
        result = await self.session.execute(
            sa.select(
                users.c.user_id,
                users.c.display_name,
                users.c.account_type,
                active_grants.c.role_code,
                active_grants.c.department_id,
            )
            .outerjoin(
                active_grants,
                sa.and_(
                    active_grants.c.user_id == users.c.user_id,
                    active_grants.c.allowed_account_type == users.c.account_type,
                ),
            )
            .where(
                users.c.external_issuer == issuer,
                users.c.external_subject == subject,
                users.c.status == "active",
            )
        )
        rows = result.mappings().all()
        if not rows:
            raise CoreError(ErrorCode.FORBIDDEN)
        first = rows[0]
        return IdentityRecord(
            id=str(first["user_id"]),
            display_name=first["display_name"],
            account_type=first["account_type"],
            grants=tuple(
                Grant(row["role_code"], str(row["department_id"]) if row["department_id"] else None)
                for row in rows
                if row["role_code"] is not None
            ),
        )
