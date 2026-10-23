from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity


class TransientTransactionConflict(CoreError):
    def __init__(self):
        super().__init__(ErrorCode.VERSION_CONFLICT)


ResourceKind = Literal[
    "department", "application", "source", "product", "contract", "request", "grant", "job"
]


@dataclass(frozen=True)
class StoredResource:
    public: dict[str, Any]
    private: dict[str, Any]


@dataclass(frozen=True)
class CommandReceipt:
    fingerprint: str
    status: int
    body: dict[str, Any]
    location: str | None = None


class CoreTransaction(Protocol):
    async def now(self) -> datetime: ...

    async def get(self, kind: ResourceKind, resource_id: UUID) -> StoredResource: ...

    async def find(self, kind: ResourceKind, **equal: Any) -> list[StoredResource]: ...

    async def claimable_jobs(self, now: datetime, *, limit: int) -> list[StoredResource]: ...

    async def insert(self, kind: ResourceKind, resource: StoredResource) -> None: ...

    async def replace(self, kind: ResourceKind, resource: StoredResource) -> None: ...

    async def receipt(self, namespace: str) -> CommandReceipt | None: ...

    async def save_receipt(self, namespace: str, receipt: CommandReceipt) -> None: ...

    async def resolve_identity(self, identity: VerifiedIdentity) -> dict[str, Any]: ...

    async def identity_for_principal(self, principal_id: UUID) -> VerifiedIdentity: ...

    async def audit(
        self,
        actor_id: UUID | None,
        correlation_id: str,
        action: str,
        resource_id: UUID,
        *,
        outcome: Literal["SUCCESS", "FAILURE", "DENIED"] = "SUCCESS",
        error_code: str | None = None,
        content_hash: str | None = None,
        resource_type: ResourceKind = "job",
        initiator_id: UUID | None = None,
    ) -> None: ...


class CoreStore(Protocol):
    durable: bool

    def transaction(
        self, *, read_only: bool = False
    ) -> AbstractAsyncContextManager[CoreTransaction]: ...

    def permission_lock(
        self, product_id: UUID, department_id: UUID
    ) -> AbstractAsyncContextManager[None]: ...
