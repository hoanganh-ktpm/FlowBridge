from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.platform.identity.authentication import VerifiedIdentity

from .schemas import (
    JobType,
    PermissionTarget,
    PostgresConnection,
    ProductRecord,
    PublicationJob,
    SelectGrant,
    SourceRecord,
    ValidatedContract,
)


class DataSourceRepository(Protocol):
    async def get(self, source_id: UUID) -> SourceRecord: ...

    async def mark_connected(self, source_id: UUID, revision: int) -> None: ...


class DataProductRepository(Protocol):
    async def get(self, product_id: UUID) -> ProductRecord: ...

    async def mark_active(self, product_id: UUID, revision: int) -> None: ...


class AccessGrantRepository(Protocol):
    async def get(self, grant_id: UUID) -> SelectGrant: ...

    def lock(self, target: PermissionTarget) -> AbstractAsyncContextManager[None]: ...

    async def list_for_target(self, target: PermissionTarget) -> list[SelectGrant]: ...

    async def mark_applied(self, grant_id: UUID, revision: int) -> None: ...

    async def mark_revoked(self, grant_id: UUID, revision: int) -> None: ...

    async def mark_failed(self, grant_id: UUID, revision: int, error_code: str) -> None: ...


class PublicationJobRepository(Protocol):
    async def enqueue(
        self,
        kind: JobType,
        target_id: UUID,
        revision: int,
        *,
        idempotency_key: str,
        requested_by: UUID | None = None,
    ) -> UUID: ...

    async def get(self, job_id: UUID) -> PublicationJob: ...

    async def claim(
        self, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> PublicationJob | None: ...

    async def renew_lease(
        self, job_id: UUID, worker_id: str, lease_expires_at: datetime, *, attempt_count: int
    ) -> None: ...

    async def mark_succeeded(
        self, job_id: UUID, *, worker_id: str, target_revision: int, attempt_count: int
    ) -> None: ...

    async def mark_failed(
        self,
        job_id: UUID,
        error_code: str,
        *,
        worker_id: str,
        target_revision: int,
        attempt_count: int,
        retry_at: datetime | None = None,
    ) -> None: ...


class SecretResolver(Protocol):
    async def resolve_postgres(self, secret_ref: str) -> PostgresConnection: ...


class IdentityResolver(Protocol):
    async def require_admin(self, actor: VerifiedIdentity, action: str) -> None: ...


class DataContractValidator(Protocol):
    async def validate(
        self, source: SourceRecord, product: ProductRecord, *, contract_id: UUID | None = None
    ) -> ValidatedContract: ...
