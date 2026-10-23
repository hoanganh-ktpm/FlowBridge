"""PostgreSQL unit-of-work adapter for the existing Core P0 schema."""

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import models
from app.core.db.commands import timestamp
from app.core.db.ports import CommandReceipt, StoredResource, TransientTransactionConflict
from app.core.governance.schemas import PUBLIC_FIELDS
from app.core.hasura_provisioner.schemas import department_role
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity

TABLES = {
    "department": models.departments,
    "application": models.applications,
    "source": models.data_sources,
    "product": models.data_products,
    "contract": models.data_contract_versions,
    "request": models.access_requests,
    "grant": models.access_grants,
    "job": models.publication_jobs,
    "relationship": models.data_product_relationships,
}
SOURCE_PRIVATE = {"host", "port", "database_name", "db_user", "ssl_mode", "secret_ref"}
JOB_PRIVATE = {"idempotency_key", "requested_by", "correlation_id", "locked_by", "lease_expires_at"}


def wire(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return timestamp(value.astimezone(UTC))
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    return value


def scalar(column, value):
    if value is None:
        return None
    if isinstance(column.type, sa.Uuid):
        return UUID(str(value))
    if isinstance(column.type, sa.DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def lock_key(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big", signed=True)


def database_error(error):
    code = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
    if code in {"40001", "40P01"}:
        return TransientTransactionConflict()
    if code == "23505":
        return CoreError(ErrorCode.VERSION_CONFLICT)
    if code in {"23503", "23514", "23502", "22023"}:
        return CoreError(ErrorCode.VALIDATION_ERROR)
    return CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)


class PostgresCoreStore:
    durable = True

    def __init__(self, engine: AsyncEngine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Core persistence requires PostgreSQL")
        self.engine = engine

    @asynccontextmanager
    async def transaction(self, *, read_only=False):
        try:
            async with self.engine.connect() as raw:
                connection = await raw.execution_options(
                    isolation_level="REPEATABLE READ" if read_only else "SERIALIZABLE"
                )
                async with connection.begin():
                    if read_only:
                        await connection.execute(sa.text("SET TRANSACTION READ ONLY"))
                    await connection.execute(sa.text("SET LOCAL statement_timeout = '20s'"))
                    await connection.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
                    yield PostgresCoreTransaction(connection, read_only=read_only)
        except DBAPIError as error:
            raise database_error(error) from None
        except (OSError, TimeoutError):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None

    @asynccontextmanager
    async def permission_lock(self, product_id: UUID, department_id: UUID):
        key = lock_key(f"flowbridge:permission:{product_id}:{department_id}")
        try:
            async with self.engine.connect() as connection:
                locked = False
                try:
                    await connection.execute(sa.text("SET statement_timeout = '20s'"))
                    await connection.execute(sa.select(sa.func.pg_advisory_lock(key)))
                    await connection.commit()
                    locked = True
                    yield
                finally:
                    if locked:
                        try:
                            await connection.execute(sa.select(sa.func.pg_advisory_unlock(key)))
                            await connection.execute(sa.text("RESET statement_timeout"))
                            await connection.commit()
                        except BaseException:
                            await connection.invalidate()
                            raise
                    else:
                        await connection.invalidate()
        except DBAPIError as error:
            raise database_error(error) from None
        except (OSError, TimeoutError):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None


class PostgresCoreTransaction:
    def __init__(self, connection, *, read_only=False):
        self.connection = connection
        self.read_only = read_only
        self.loaded = {}

    async def now(self):
        return await self.connection.scalar(sa.select(sa.func.clock_timestamp()))

    async def projection(self, kind, row):
        values = dict(row)
        private_fields = (
            SOURCE_PRIVATE if kind == "source" else JOB_PRIVATE if kind == "job" else set()
        )
        private = {key: wire(values[key]) for key in private_fields}
        public = wire(values)
        if kind == "product":
            source = models.data_sources
            application = models.applications
            department_id = await self.connection.scalar(
                sa.select(application.c.department_id)
                .select_from(source.join(application, source.c.application_id == application.c.id))
                .where(source.c.id == values["data_source_id"])
            )
            if department_id is None:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            public["owner_department_id"] = str(department_id)
        fields = PUBLIC_FIELDS.get(kind, set(values))
        resource = StoredResource({key: public[key] for key in fields}, private)
        self.loaded[(kind, str(values["id"]))] = values
        return resource

    async def get(self, kind, resource_id):
        table = TABLES[kind]
        query = sa.select(table).where(table.c.id == UUID(str(resource_id)))
        if not self.read_only:
            query = query.with_for_update()
        row = (await self.connection.execute(query)).mappings().one_or_none()
        if row is None:
            raise CoreError(ErrorCode.NOT_FOUND)
        return await self.projection(kind, row)

    async def find(self, kind, **equal):
        table = TABLES[kind]
        if any(key not in table.c for key in equal):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        query = (
            sa.select(table)
            .where(*(table.c[key] == scalar(table.c[key], value) for key, value in equal.items()))
            .order_by(table.c.id)
            .limit(1001)
        )
        if not self.read_only:
            query = query.with_for_update()
        rows = (await self.connection.execute(query)).mappings().all()
        if len(rows) > 1000:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return [await self.projection(kind, row) for row in rows]

    async def claimable_jobs(self, now, *, limit):
        if not 1 <= limit <= 100:
            raise ValueError("Bounded job claims required")
        table = models.publication_jobs
        due = sa.or_(
            sa.and_(table.c.status.in_(["QUEUED", "RETRY"]), table.c.next_attempt_at <= now),
            sa.and_(table.c.status == "RUNNING", table.c.lease_expires_at <= now),
        )
        rows = (
            (
                await self.connection.execute(
                    sa.select(table)
                    .where(due)
                    .order_by(table.c.next_attempt_at, table.c.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .mappings()
            .all()
        )
        return [await self.projection("job", row) for row in rows]

    def values(self, kind, resource):
        table = TABLES[kind]
        combined = {**resource.public, **resource.private}
        values = {
            column.name: scalar(column, combined[column.name])
            for column in table.c
            if column.name in combined
        }
        if kind == "grant":
            values["enforcement_role"] = department_role(UUID(combined["consumer_department_id"]))
        return values

    async def insert(self, kind, resource):
        values = self.values(kind, resource)
        await self.connection.execute(TABLES[kind].insert().values(**values))
        self.loaded[(kind, str(values["id"]))] = values

    async def replace(self, kind, resource):
        table = TABLES[kind]
        resource_id = str(resource.public["id"])
        previous = self.loaded.get((kind, resource_id))
        if previous is None:
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        values = self.values(kind, resource)
        if kind == "contract" and previous["status"] in {"PUBLISHED", "RETIRED"}:
            if any(
                values[key] != previous[key]
                for key in ("contract_json", "version", "data_product_id")
            ):
                raise CoreError(ErrorCode.INVALID_STATE)
        conditions = [table.c.id == UUID(resource_id)]
        for field in (
            "revision",
            "desired_revision",
            "attempt_count",
            "status",
            "locked_by",
            "lease_expires_at",
        ):
            if field in previous and field in table.c:
                conditions.append(table.c[field] == previous[field])
        result = await self.connection.execute(table.update().where(*conditions).values(**values))
        if result.rowcount != 1:
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        self.loaded[(kind, resource_id)] = values

    async def receipt(self, namespace):
        await self.connection.execute(sa.select(sa.func.pg_advisory_xact_lock(lock_key(namespace))))
        table = models.core_command_receipts
        row = (
            (
                await self.connection.execute(
                    sa.select(table).where(table.c.namespace == namespace).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return CommandReceipt(row["fingerprint"], row["status"], row["body"], row["location"])

    async def save_receipt(self, namespace, receipt):
        now = await self.now()
        await self.connection.execute(
            models.core_command_receipts.insert().values(
                namespace=namespace,
                fingerprint=receipt.fingerprint,
                status=receipt.status,
                body=receipt.body,
                location=receipt.location,
                created_at=now,
                expires_at=now + timedelta(days=7),
            )
        )

    async def resolve_identity(self, identity):
        principal = models.principals
        row = (
            (
                await self.connection.execute(
                    sa.select(principal).where(
                        principal.c.issuer == identity.issuer,
                        principal.c.subject == identity.subject,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CoreError(ErrorCode.FORBIDDEN)
        memberships = models.department_memberships
        department = models.departments
        rows = (
            (
                await self.connection.execute(
                    sa.select(memberships, department.c.status.label("department_status"))
                    .join(department, memberships.c.department_id == department.c.id)
                    .where(memberships.c.principal_id == row["id"])
                    .limit(101)
                )
            )
            .mappings()
            .all()
        )
        if len(rows) > 100:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return {
            "issuer": row["issuer"],
            "subject": row["subject"],
            "kind": row["kind"],
            "service_client_id": row["service_client_id"],
            "context": {
                "principal_id": row["id"],
                "principal_status": row["status"],
                "platform_role": row["platform_role"],
                "application_id": row["application_id"],
                "memberships": [
                    {
                        key: value
                        for key, value in item.items()
                        if key
                        in {
                            "department_id",
                            "membership_role",
                            "status",
                            "expires_at",
                            "department_status",
                        }
                    }
                    for item in rows
                ],
            },
        }

    async def identity_for_principal(self, principal_id):
        table = models.principals
        row = (
            (await self.connection.execute(sa.select(table).where(table.c.id == principal_id)))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CoreError(ErrorCode.FORBIDDEN)
        return VerifiedIdentity(
            issuer=row["issuer"],
            subject=row["subject"],
            account_type=row["kind"],
            client_id=row["service_client_id"],
        )

    async def audit(
        self,
        actor_id,
        correlation_id,
        action,
        resource_id,
        *,
        outcome="SUCCESS",
        error_code=None,
        content_hash=None,
        resource_type="job",
        initiator_id=None,
        details=None,
    ):
        kind = "system"
        if actor_id is not None:
            kind = await self.connection.scalar(
                sa.select(models.principals.c.kind).where(models.principals.c.id == actor_id)
            )
            if kind is None:
                raise CoreError(ErrorCode.FORBIDDEN)
        await self.connection.execute(
            models.audit_events.insert().values(
                id=uuid4(),
                occurred_at=await self.now(),
                actor_principal_id=actor_id,
                actor_kind=kind,
                initiator_principal_id=initiator_id,
                action=action,
                resource_type=resource_type,
                resource_ref=str(resource_id),
                outcome=outcome,
                request_id=correlation_id,
                error_code=error_code,
                content_hash=content_hash,
                diff_json=details,
            )
        )
