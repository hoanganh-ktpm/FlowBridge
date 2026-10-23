from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.core.db.ports import CoreTransaction
from app.core.hasura_provisioner.schemas import DTO
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity


class Membership(DTO):
    department_id: UUID
    membership_role: Literal["STAFF", "OWNER"]
    status: Literal["ACTIVE", "REVOKED"]
    expires_at: datetime | None
    department_status: Literal["ACTIVE", "DISABLED"]

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value


class PrincipalContext(DTO):
    principal_id: UUID
    principal_status: Literal["ACTIVE", "DISABLED"]
    platform_role: Literal["NONE", "PLATFORM_ADMIN"]
    application_id: UUID | None
    memberships: tuple[Membership, ...] = Field(max_length=100)

    def require_admin(self) -> None:
        if self.principal_status != "ACTIVE" or self.platform_role != "PLATFORM_ADMIN":
            raise CoreError(ErrorCode.FORBIDDEN)

    def departments(self, now: datetime) -> frozenset[UUID]:
        return frozenset(
            item.department_id
            for item in self.memberships
            if item.status == item.department_status == "ACTIVE"
            and (item.expires_at is None or item.expires_at > now)
        )


class PrincipalBinding(DTO):
    issuer: str
    subject: str
    kind: Literal["human", "service"]
    service_client_id: str | None
    context: PrincipalContext


async def resolve_principal(
    transaction: CoreTransaction, identity: VerifiedIdentity
) -> PrincipalContext:
    binding = PrincipalBinding.model_validate(await transaction.resolve_identity(identity))
    principal = binding.context
    if (
        binding.issuer != identity.issuer
        or binding.subject != identity.subject
        or binding.kind != identity.account_type
    ):
        raise CoreError(ErrorCode.FORBIDDEN)
    if principal.principal_status != "ACTIVE":
        raise CoreError(ErrorCode.FORBIDDEN)
    if identity.account_type == "human" and (
        principal.application_id is not None or binding.service_client_id is not None
    ):
        raise CoreError(ErrorCode.FORBIDDEN)
    if identity.account_type == "service":
        if not identity.client_id or binding.service_client_id != identity.client_id:
            raise CoreError(ErrorCode.FORBIDDEN)
    if principal.application_id is not None:
        application = await transaction.get("application", principal.application_id)
        if application.public["status"] != "ACTIVE":
            raise CoreError(ErrorCode.FORBIDDEN)
        department = await transaction.get("department", UUID(application.public["department_id"]))
        if department.public["status"] != "ACTIVE":
            raise CoreError(ErrorCode.FORBIDDEN)
    return principal


class CoreAdminResolver:
    def __init__(self, store) -> None:
        self._store = store

    async def require_admin(self, actor: VerifiedIdentity, action: str) -> None:
        async with self._store.transaction() as transaction:
            (await resolve_principal(transaction, actor)).require_admin()
