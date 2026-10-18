# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Authorization derives from active database grants, never caller-supplied roles or user IDs.
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.repository import SupplierRepository, table


def require(condition: bool, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    if not condition:
        raise CoreError(code)


class SupplierPolicy:
    def __init__(self, repository: SupplierRepository) -> None:
        self.repository = repository
        self.user: dict[str, Any] = {}
        self.grants: dict[str, list[dict[str, Any]]] = {}
        self.action_roles: set[str] = set()

    async def authenticate(self, identity: VerifiedIdentity) -> None:
        users = table("users")
        matches = await self.repository.rows(
            "users",
            users.c.external_issuer == identity.issuer,
            users.c.external_subject == identity.subject,
        )
        require(bool(matches), ErrorCode.FORBIDDEN)
        self.user = matches[0]
        require(self.user["status"] == "active", ErrorCode.FORBIDDEN)
        require(self.user["account_type"] == identity.account_type, ErrorCode.FORBIDDEN)
        roles = {row["role_id"]: row for row in await self.repository.rows("roles")}
        grants = await self.repository.rows(
            "user_roles",
            table("user_roles").c.user_id == self.user["user_id"],
            table("user_roles").c.revoked_at.is_(None),
        )
        for grant in grants:
            role = roles.get(grant["role_id"])
            if role is None or role["status"] != "active":
                continue
            code = role["role_code"]
            account = "service" if code in {"AI_SERVICE", "PO_SERVICE"} else "human"
            scope = "department" if code in {"APPROVER", "WAREHOUSE"} else "global"
            if (
                self.user["account_type"] != account
                or role["allowed_account_type"] != account
                or role["scope_type"] != scope
                or grant["scope_type"] != scope
                or (scope == "global" and grant["department_id"] is not None)
                or (scope == "department" and grant["department_id"] is None)
            ):
                continue
            self.grants.setdefault(code, []).append(grant)

    def has(self, role: str) -> bool:
        return bool(self.grants.get(role))

    def authorize_profile(self) -> None:
        # Reading one's own grants must also work for an ADMIN-only account.
        # This does not grant access to employee records or purchase operations.
        allowed = {"EMPLOYEE", "APPROVER", "WAREHOUSE", "ADMIN"}
        require(
            self.user["account_type"] == "human" and any(self.has(code) for code in allowed),
            ErrorCode.FORBIDDEN,
        )
        self.action_roles = allowed

    def authorize(self, role: str, *, read: bool = False) -> None:
        allowed = {role}
        if role == "EMPLOYEE" and read:
            allowed |= {"APPROVER", "WAREHOUSE"}
        require(any(self.has(code) for code in allowed), ErrorCode.FORBIDDEN)
        self.action_roles = allowed

    def request_conditions(self, *, roles: set[str] | None = None) -> list[Any]:
        requests = table("purchase_requests")
        checks = []
        allowed = roles if roles is not None else self.action_roles
        if "EMPLOYEE" in allowed and self.has("EMPLOYEE"):
            checks.append(requests.c.requested_by == self.user["user_id"])
        for role in ("APPROVER", "WAREHOUSE"):
            if role in allowed:
                checks.extend(
                    requests.c.department_id == grant["department_id"]
                    for grant in self.grants.get(role, [])
                )
        if allowed & {"AI_SERVICE", "PO_SERVICE"} & self.grants.keys():
            checks.append(sa.true())
        return [sa.or_(*checks) if checks else sa.false()]

    async def request(
        self,
        identifier: Any,
        *,
        lock: bool = False,
        roles: set[str] | None = None,
    ) -> dict[str, Any]:
        return await self.repository.one(
            "purchase_requests",
            table("purchase_requests").c.request_id == UUID(str(identifier)),
            *self.request_conditions(roles=roles),
            lock=lock,
        )
