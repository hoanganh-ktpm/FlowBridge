# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Explicit operator bootstrap; caller must commit or roll back the complete transaction.
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from flowbridge.identity.accounts import IdentityAccounts
from flowbridge.identity.directory import DirectoryIdentity
from flowbridge.shared.errors import ErrorCode
from flowbridge.supplier_assistant.database_service import SupplierDatabaseService
from flowbridge.supplier_assistant.policy import require
from flowbridge.supplier_assistant.repository import SupplierRepository, table


async def bootstrap_identity(
    repo: SupplierRepository,
    identity: DirectoryIdentity,
    *,
    roles: list[str],
    department_id: UUID | None = None,
    granted_by: UUID | None = None,
    first_admin: bool = False,
    display_name: str | None = None,
) -> dict[str, Any]:
    require(
        all(
            code in {"ADMIN", "EMPLOYEE", "APPROVER", "WAREHOUSE", "AI_SERVICE", "PO_SERVICE"}
            for code in roles
        ),
        ErrorCode.VALIDATION_ERROR,
    )
    require(len(roles) == len(set(roles)), ErrorCode.VALIDATION_ERROR)
    require("EMPLOYEE" not in roles or department_id is not None, ErrorCode.VALIDATION_ERROR)
    await repo.session.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtext('flowbridge.identity.bootstrap'))")
    )
    users, grants, role_table = table("users"), table("user_roles"), table("roles")
    admins = (
        (
            await repo.session.execute(
                sa.select(users.c.user_id)
                .join(grants, grants.c.user_id == users.c.user_id)
                .join(role_table, role_table.c.role_id == grants.c.role_id)
                .where(
                    users.c.status == "active",
                    users.c.account_type == "human",
                    role_table.c.role_code == "ADMIN",
                    role_table.c.status == "active",
                    grants.c.revoked_at.is_(None),
                    grants.c.scope_type == "global",
                    grants.c.department_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if first_admin:
        require(roles == ["ADMIN"] and identity.account_type == "human", ErrorCode.VALIDATION_ERROR)
        if admins:
            existing = await repo.rows(
                "users",
                users.c.external_issuer == identity.issuer,
                users.c.external_subject == identity.subject,
            )
            require(bool(existing) and set(admins) == {existing[0]["user_id"]}, ErrorCode.FORBIDDEN)
    else:
        require(granted_by is not None and granted_by in admins, ErrorCode.FORBIDDEN)
    accounts = IdentityAccounts(repo)
    user, created = await accounts.link(
        identity,
        display_name=display_name or identity.display_name,
        department_id=department_id,
        email=identity.email,
    )
    actor = user["user_id"] if first_admin else granted_by
    granted = 0
    for code in roles:
        _, added = await accounts.grant(user, code, department_id=department_id, granted_by=actor)
        granted += int(added)
    if created or granted:
        await SupplierDatabaseService(repo).event(
            "user",
            user["user_id"],
            "identity.bootstrap_applied",
            {"user_id": str(user["user_id"]), "actor_id": str(actor), "roles": roles},
        )
    return {"user_id": str(user["user_id"]), "created": created, "grants_added": granted}
