# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Link a directory-verified identity in a caller-owned database transaction.
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from flowbridge.identity.account_repository import IdentityAccountRepository
from flowbridge.identity.directory import DirectoryIdentity
from flowbridge.shared.errors import CoreError, ErrorCode


def require(condition: bool, code: ErrorCode) -> None:
    if not condition:
        raise CoreError(code)


class IdentityAccounts:
    def __init__(self, repository: IdentityAccountRepository) -> None:
        self.repo = repository

    async def link(
        self,
        identity: DirectoryIdentity,
        *,
        display_name: str,
        department_id: UUID | None = None,
        email: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        require(bool(display_name.strip()) and len(display_name) <= 255, ErrorCode.VALIDATION_ERROR)
        if department_id is not None:
            department = await self.repo.department(department_id)
            require(department["status"] == "active", ErrorCode.VALIDATION_ERROR)
        await self.repo.lock_identity(identity.issuer, identity.subject)
        existing = await self.repo.find_identity(identity.issuer, identity.subject)
        if existing:
            require(existing["account_type"] == identity.account_type, ErrorCode.FORBIDDEN)
            require(existing["status"] == "active", ErrorCode.FORBIDDEN)
            # Retrying a link never changes profile, department, status or grants.
            return existing, False
        timestamp = datetime.now(UTC)
        return await self.repo.insert_user(
            {
                "user_id": uuid4(),
                "external_issuer": identity.issuer,
                "external_subject": identity.subject,
                "display_name": display_name,
                "email": email,
                "department_id": department_id,
                "account_type": identity.account_type,
                "status": "active",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        ), True

    async def grant(
        self,
        user: dict[str, Any],
        code: str,
        *,
        department_id: UUID | None,
        granted_by: UUID,
    ) -> tuple[dict[str, Any], bool]:
        role = await self.repo.role(code)
        account = "service" if code in {"AI_SERVICE", "PO_SERVICE"} else "human"
        scope = "department" if code in {"APPROVER", "WAREHOUSE"} else "global"
        require(role["status"] == user["status"] == "active", ErrorCode.FORBIDDEN)
        require(
            user["account_type"] == role["allowed_account_type"] == account, ErrorCode.FORBIDDEN
        )
        require(role["scope_type"] == scope, ErrorCode.FORBIDDEN)
        require(scope != "department" or department_id is not None, ErrorCode.VALIDATION_ERROR)
        if scope == "department":
            department = await self.repo.department(department_id)
            require(department["status"] == "active", ErrorCode.VALIDATION_ERROR)
        scope_department = department_id if scope == "department" else None
        existing = await self.repo.active_grant(user["user_id"], role["role_id"], scope_department)
        if existing:
            return existing, False
        return await self.repo.insert_grant(
            {
                "user_role_id": uuid4(),
                "user_id": user["user_id"],
                "role_id": role["role_id"],
                "scope_type": scope,
                "department_id": scope_department,
                "granted_by": granted_by,
                "granted_at": datetime.now(UTC),
                "revoked_by": None,
                "revoked_at": None,
            },
        ), True
