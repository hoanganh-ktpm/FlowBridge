# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Link a directory-verified identity in a caller-owned database transaction.
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from flowbridge.identity.directory import DirectoryIdentity
from flowbridge.shared.errors import ErrorCode
from flowbridge.supplier_assistant.policy import require
from flowbridge.supplier_assistant.repository import SupplierRepository, table


class IdentityAccounts:
    def __init__(self, repository: SupplierRepository) -> None:
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
            department = await self.repo.get("departments", department_id)
            require(department["status"] == "active", ErrorCode.VALIDATION_ERROR)
        await self.repo.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": "identity:" + identity.issuer + ":" + identity.subject},
        )
        users = table("users")
        existing = await self.repo.rows(
            "users",
            users.c.external_issuer == identity.issuer,
            users.c.external_subject == identity.subject,
            lock=True,
        )
        if existing:
            require(existing[0]["account_type"] == identity.account_type, ErrorCode.FORBIDDEN)
            require(existing[0]["status"] == "active", ErrorCode.FORBIDDEN)
            # Retrying a link never changes profile, department, status or grants.
            return existing[0], False
        timestamp = datetime.now(UTC)
        return await self.repo.insert(
            "users",
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
        role = await self.repo.one("roles", table("roles").c.role_code == code)
        account = "service" if code in {"AI_SERVICE", "PO_SERVICE"} else "human"
        scope = "department" if code in {"APPROVER", "WAREHOUSE"} else "global"
        require(role["status"] == user["status"] == "active", ErrorCode.FORBIDDEN)
        require(
            user["account_type"] == role["allowed_account_type"] == account, ErrorCode.FORBIDDEN
        )
        require(role["scope_type"] == scope, ErrorCode.FORBIDDEN)
        require(scope != "department" or department_id is not None, ErrorCode.VALIDATION_ERROR)
        if scope == "department":
            department = await self.repo.get("departments", department_id)
            require(department["status"] == "active", ErrorCode.VALIDATION_ERROR)
        scope_department = department_id if scope == "department" else None
        grants = table("user_roles")
        conditions = [
            grants.c.user_id == user["user_id"],
            grants.c.role_id == role["role_id"],
            grants.c.revoked_at.is_(None),
            grants.c.department_id == scope_department,
        ]
        existing = await self.repo.rows("user_roles", *conditions, lock=True)
        if existing:
            return existing[0], False
        return await self.repo.insert(
            "user_roles",
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
