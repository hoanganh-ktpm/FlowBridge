import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.applications.manifest import fingerprint
from app.core.governance.policy import PrincipalContext, resolve_principal
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity

from .ports import (
    CommandReceipt,
    CoreStore,
    CoreTransaction,
    StoredResource,
    TransientTransactionConflict,
)


def timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def uuid(value: str | UUID) -> UUID:
    return UUID(str(value))


def require_revision(resource: StoredResource, expected: int, field: str = "revision") -> None:
    if resource.public[field] != expected:
        raise CoreError(ErrorCode.VERSION_CONFLICT)


def require_state(condition: bool) -> None:
    if not condition:
        raise CoreError(ErrorCode.INVALID_STATE)


@dataclass
class CommandContext:
    transaction: CoreTransaction
    principal: PrincipalContext
    now: datetime
    correlation_id: str

    def resource(self, values: dict, *, private: dict | None = None) -> StoredResource:
        return StoredResource(
            {
                "id": str(uuid4()),
                "created_at": timestamp(self.now),
                "updated_at": timestamp(self.now),
                "created_by": str(self.principal.principal_id),
                "updated_by": str(self.principal.principal_id),
                "revision": 1,
                **values,
            },
            private or {},
        )

    def update(self, resource: StoredResource, **changes: Any) -> StoredResource:
        return StoredResource(
            {
                **resource.public,
                "revision": resource.public["revision"] + 1,
                "updated_at": timestamp(self.now),
                "updated_by": str(self.principal.principal_id),
                **changes,
            },
            resource.private,
        )


class CommandExecutor:
    def __init__(self, store: CoreStore) -> None:
        self.store = store

    async def execute(self, identity, method, path, key, payload, correlation_id, operation):
        for attempt in range(3):
            try:
                return await self._execute_once(
                    identity, method, path, key, payload, correlation_id, operation
                )
            except TransientTransactionConflict:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.02 * (attempt + 1))
        raise AssertionError("Unreachable")

    async def _execute_once(
        self,
        identity: VerifiedIdentity,
        method: str,
        path: str,
        key: str,
        payload: dict,
        correlation_id: str,
        operation: Callable[[CommandContext], Awaitable[CommandReceipt]],
    ) -> CommandReceipt:
        namespace = fingerprint([identity.issuer, identity.subject, method, path, key])
        content_hash = fingerprint(payload)
        async with self.store.transaction() as transaction:
            principal = await resolve_principal(transaction, identity)
            principal.require_admin()
            previous = await transaction.receipt(namespace)
            if previous:
                if previous.fingerprint != content_hash:
                    raise CoreError(ErrorCode.IDEMPOTENCY_CONFLICT)
                return previous
            context = CommandContext(
                transaction, principal, await transaction.now(), correlation_id
            )
            result = await operation(context)
            receipt = CommandReceipt(content_hash, result.status, result.body, result.location)
            await transaction.save_receipt(namespace, receipt)
            return receipt
