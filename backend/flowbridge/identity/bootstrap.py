# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Explicit operator bootstrap; caller must commit or roll back the complete transaction.
from typing import Any
from uuid import UUID

from flowbridge.identity.account_repository import IdentityAccountRepository
from flowbridge.identity.accounts import IdentityAccounts, require
from flowbridge.identity.audit import IdentityAuditSink, IdentityOutbox
from flowbridge.identity.directory import DirectoryIdentity
from flowbridge.shared.errors import ErrorCode


async def bootstrap_identity(
    repo: IdentityAccountRepository,
    identity: DirectoryIdentity,
    *,
    roles: list[str],
    audit: IdentityAuditSink | None = None,
    department_id: UUID | None = None,
    granted_by: UUID | None = None,
    first_admin: bool = False,
    display_name: str | None = None,
) -> dict[str, Any]:
    require(
        all(
            code
            in {
                "ADMIN",
                "EMPLOYEE",
                "APPROVER",
                "WAREHOUSE",
                "AI_SERVICE",
                "PO_SERVICE",
                "PROCUREMENT_VIEWER",
            }
            for code in roles
        ),
        ErrorCode.VALIDATION_ERROR,
    )
    require(len(roles) == len(set(roles)), ErrorCode.VALIDATION_ERROR)
    require("EMPLOYEE" not in roles or department_id is not None, ErrorCode.VALIDATION_ERROR)
    admins = await repo.active_admins()
    if first_admin:
        require(roles == ["ADMIN"] and identity.account_type == "human", ErrorCode.VALIDATION_ERROR)
        if admins:
            existing = await repo.find_identity(identity.issuer, identity.subject)
            require(bool(existing) and set(admins) == {existing["user_id"]}, ErrorCode.FORBIDDEN)
    else:
        require(granted_by is not None and granted_by in admins, ErrorCode.FORBIDDEN)
    if "PROCUREMENT_VIEWER" in roles:
        require(identity.account_type == "human", ErrorCode.FORBIDDEN)
        await repo.ensure_procurement_reader_role()
    accounts = IdentityAccounts(repo)
    user, created = await accounts.link(
        identity,
        display_name=display_name or identity.display_name,
        department_id=department_id,
        email=identity.email,
    )
    actor = user["user_id"] if first_admin else granted_by
    assert actor is not None
    granted = 0
    for code in roles:
        _, added = await accounts.grant(user, code, department_id=department_id, granted_by=actor)
        granted += int(added)
    if created or granted:
        await (audit or IdentityOutbox(repo.session)).bootstrap_applied(
            user["user_id"], actor, roles
        )
    return {"user_id": str(user["user_id"]), "created": created, "grants_added": granted}
