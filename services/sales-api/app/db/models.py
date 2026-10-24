"""Three independent business databases, frozen from the v2 handoff DBML.

Warehouse is authoritative for inventory and physical carrier handoff. Logistics
acceptance is planning; neither it nor Sales establishes customer delivery.
Only local, organization-qualified references are foreign keys. Cross-database
UUIDs are opaque and must be checked through authorized business interfaces.

CHECK constraints here enforce row invariants. Aggregate stock/hold equality,
manifest totals, confirmed snapshot immutability, event ordering, authorization
and lifecycle transitions require locked business transactions. ORM writes alone
are not an implementation of those services. No roles or passwords live here.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class SalesBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _name(value: str) -> str:
    """Keep PostgreSQL identifiers deterministic and within its 63-byte limit."""
    if len(value) <= 63:
        return value
    return f"{value[:54]}_{hashlib.sha256(value.encode()).hexdigest()[:8]}"


def _uq(table: str, *columns: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint(*columns, name=_name(f"uq_{table}_{'_'.join(columns)}"))


def _ck(table: str, name: str, sql: str) -> sa.CheckConstraint:
    # Base metadata prefixes the table name via its ck naming convention.
    return sa.CheckConstraint(sql, name=_name(name))


def _fk(
    table: str, columns: tuple[str, ...], target: str, refs: tuple[str, ...]
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        columns,
        [f"public.{target}.{column}" for column in refs],
        name=_name(f"fk_{table}_{'_'.join(columns)}_{target}"),
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


def _ix(table: str, *columns: str, **kwargs: Any) -> sa.Index:
    return sa.Index(_name(f"ix_{table}_{'_'.join(columns)}"), *columns, **kwargs)


def _nonblank(table: str, *columns: str) -> sa.CheckConstraint:
    return _ck(table, "nonblank", " AND ".join(f"btrim({column}) <> ''" for column in columns))


def _args(table: str, *constraints: Any, comment: str) -> tuple[Any, ...]:
    return (
        _uq(table, "org_id", "id"),
        *constraints,
        {"schema": "public", "comment": comment},
    )


def _time(**kwargs: Any) -> Any:
    return mapped_column(sa.DateTime(timezone=True), **kwargs)


def _created() -> Any:
    return _time(nullable=False, server_default=sa.func.now())


def _version() -> Any:
    return mapped_column(sa.BigInteger, nullable=False, server_default=sa.text("1"))


def _quantity(default: str | None = None) -> Any:
    kwargs: dict[str, Any] = {"nullable": False}
    if default is not None:
        kwargs["server_default"] = sa.text(default)
    return mapped_column(sa.Numeric(18, 3), **kwargs)


def _hash_check(table: str, column: str) -> sa.CheckConstraint:
    return _ck(table, f"{column}_sha256", f"{column} ~ '^[0-9a-f]{{64}}$'")


class _OrgRow:
    id: Mapped[UUID] = mapped_column(
        sa.Uuid,
        primary_key=True,
        default=uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    org_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)


SALES_STATES = "'DRAFT', 'SUBMITTED', 'CONFIRMED', 'WAITING_STOCK', 'RESERVED', 'FULFILLING', 'PARTIALLY_HANDED_OVER', 'COMPLETED', 'CANCEL_PENDING', 'CANCELLED', 'EXCEPTION'"
SHIPMENT_STATES = "'DRAFT', 'ACCEPTED', 'HANDED_OVER', 'CANCELLED', 'EXCEPTION'"


class _CommandReceipt(_OrgRow):
    scope: Mapped[str] = mapped_column(sa.String(180), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    actor_subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, server_default="PROCESSING")
    resource_type: Mapped[str | None] = mapped_column(sa.String(80))
    resource_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    response_status: Mapped[int | None] = mapped_column(sa.Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="Private cached result may contain PII; not a public dataset."
    )
    error_code: Mapped[str | None] = mapped_column(sa.String(80))
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = _time()

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        return _args(
            "command_receipts",
            _uq("command_receipts", "org_id", "scope", "idempotency_key"),
            _ck(
                "command_receipts",
                "status",
                "status IN ('PROCESSING', 'SUCCEEDED', 'FAILED')",
            ),
            _hash_check("command_receipts", "request_hash"),
            _nonblank("command_receipts", "scope", "idempotency_key", "actor_subject"),
            _ck(
                "command_receipts",
                "response_status",
                "response_status BETWEEN 100 AND 599",
            ),
            _ck(
                "command_receipts",
                "resource_pair",
                "(resource_type IS NULL) = (resource_id IS NULL)",
            ),
            _ck(
                "command_receipts",
                "completion_marker",
                "(status IN ('SUCCEEDED', 'FAILED')) = (completed_at IS NOT NULL)",
            ),
            comment="Local atomic command/idempotency record. Authorize actor/scope before returning cached result; different canonical request hash conflicts. Domain mutation, final receipt and outbox commit together. Concurrent duplicates need lock/claim. Private response may contain PII.",
        )


class _OutboxEvent(_OrgRow):
    event_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    event_schema_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    aggregate_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    business_key: Mapped[str] = mapped_column(sa.String(150), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    causation_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Authorized minimal event body; never credentials.",
    )
    payload_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, server_default="PENDING")
    attempt_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    next_attempt_at: Mapped[datetime | None] = _time()
    locked_by: Mapped[str | None] = mapped_column(sa.String(128))
    locked_until: Mapped[datetime | None] = _time()
    last_error_code: Mapped[str | None] = mapped_column(sa.String(80))
    occurred_at: Mapped[datetime] = _time(nullable=False)
    published_at: Mapped[datetime | None] = _time()

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        return _args(
            "outbox_events",
            _uq(
                "outbox_events",
                "org_id",
                "aggregate_type",
                "aggregate_id",
                "aggregate_version",
                "event_type",
            ),
            _ix("outbox_events", "status", "next_attempt_at"),
            _ck(
                "outbox_events",
                "status",
                "status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED')",
            ),
            _ck(
                "outbox_events",
                "versions",
                "event_schema_version > 0 AND aggregate_version > 0 AND attempt_count >= 0",
            ),
            _hash_check("outbox_events", "payload_hash"),
            _nonblank("outbox_events", "event_type", "aggregate_type", "business_key"),
            _ck(
                "outbox_events",
                "lease_pair",
                "(locked_by IS NULL) = (locked_until IS NULL)",
            ),
            _ck(
                "outbox_events",
                "processing_lease",
                "status <> 'PROCESSING' OR (locked_by IS NOT NULL AND btrim(locked_by) <> '' AND locked_until IS NOT NULL)",
            ),
            _ck(
                "outbox_events",
                "published_marker",
                "(status = 'PUBLISHED') = (published_at IS NOT NULL)",
            ),
            comment="Local transactional outbox, stable event ID and canonical hash across retries. At-least-once transport with leases/backoff; PUBLISHED means destination accepted, not domain completion. No cross-database ACID or unrestricted payloads. Worker implementation separate.",
        )


class _InboxReceipt(_OrgRow):
    producer_app: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    event_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    event_schema_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    consumer: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Private scoped minimal authorized body retained for durable retry; no credentials/public exposure.",
    )
    payload_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, server_default="RECEIVED")
    attempt_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    next_attempt_at: Mapped[datetime | None] = _time()
    last_error_code: Mapped[str | None] = mapped_column(sa.String(80))
    received_at: Mapped[datetime] = _created()
    processed_at: Mapped[datetime | None] = _time()

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        return _args(
            "inbox_receipts",
            _uq("inbox_receipts", "org_id", "producer_app", "event_id", "consumer"),
            _ix(
                "inbox_receipts",
                "org_id",
                "producer_app",
                "aggregate_type",
                "aggregate_id",
                "aggregate_version",
            ),
            _ix("inbox_receipts", "status", "next_attempt_at"),
            _ck(
                "inbox_receipts",
                "status",
                "status IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'DEFERRED', 'FAILED')",
            ),
            _ck(
                "inbox_receipts",
                "versions",
                "event_schema_version > 0 AND aggregate_version > 0 AND attempt_count >= 0",
            ),
            _hash_check("inbox_receipts", "payload_hash"),
            _nonblank(
                "inbox_receipts",
                "producer_app",
                "event_type",
                "consumer",
                "aggregate_type",
            ),
            _ck(
                "inbox_receipts",
                "processed_marker",
                "(status = 'PROCESSED') = (processed_at IS NOT NULL)",
            ),
            comment="Durable local receipt/dedup/retry before ACK. Validate sender/org/schema/hash/permission. Same event+different hash is rejected; domain writes/derived outbox/PROCESSED commit together. Defer gaps; retain cancellation fences even before shipment exists. Retention must not erase still-replayable fences. Private payload, not a public dataset.",
        )


class WorkflowOrder(_OrgRow, SalesBase):
    __tablename__ = "wf_e2e_orders"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "order_number"),
        _ck(__tablename__, "prefix", "order_number LIKE 'wf_e2e_%'"),
        _ck(__tablename__, "status", "status IN ('CONFIRMED','WAITING_STOCK','RESERVED','PACKED','SHIPPED','DELIVERED','FAILED')"),
        _ck(__tablename__, "version", "version > 0"),
        comment="Isolated delivery workflow slice; does not reinterpret historical carrier-handoff Sales completion. Lines are immutable authorized command snapshots, not cross-database foreign keys.",
    )
    order_number: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    warehouse_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    lines: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    owner_subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    workflow_instance_ref: Mapped[str | None] = mapped_column(sa.String(128))
    last_source_event_ref: Mapped[UUID | None] = mapped_column(sa.Uuid)
    version: Mapped[int] = _version()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class Customer(_OrgRow, SalesBase):
    __tablename__ = "customers"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "customer_code"),
        _ck(__tablename__, "status", "status IN ('ACTIVE', 'INACTIVE')"),
        _nonblank(__tablename__, "customer_code", "name", "created_by_subject"),
        _ck(__tablename__, "code_trimmed", "customer_code = btrim(customer_code)"),
        comment="Sales-owned customer PII. Approved fulfillment datasets omit email/phone unless separately authorized. No local credentials or roles.",
    )

    customer_code: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(sa.String(254))
    phone: Mapped[str | None] = mapped_column(sa.String(40))
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, server_default="ACTIVE")
    created_by_subject: Mapped[str] = mapped_column(
        sa.String(255),
        nullable=False,
        comment="OIDC subject; no local password or role.",
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class SalesOrder(_OrgRow, SalesBase):
    __tablename__ = "sales_orders"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "order_number"),
        _fk(__tablename__, ("org_id", "customer_id"), "customers", ("org_id", "id")),
        _ix(__tablename__, "org_id", "customer_id", "created_at"),
        _ix(__tablename__, "org_id", "status", "created_at"),
        _ck(__tablename__, "status", f"status IN ({SALES_STATES})"),
        _ck(__tablename__, "versions", "version > 0 AND input_revision > 0"),
        _ck(__tablename__, "currency", "currency ~ '^[A-Z]{3}$'"),
        _nonblank(__tablename__, "order_number", "created_by_subject"),
        _ck(
            __tablename__,
            "confirmation_time",
            "status NOT IN ('CONFIRMED', 'WAITING_STOCK', 'RESERVED', 'FULFILLING', 'PARTIALLY_HANDED_OVER', 'COMPLETED', 'CANCEL_PENDING') OR confirmed_at IS NOT NULL",
        ),
        _ck(
            __tablename__,
            "confirmed_snapshot",
            "confirmed_at IS NULL OR (requested_handoff_at IS NOT NULL AND fulfillment_warehouse_ref IS NOT NULL AND recipient_name IS NOT NULL AND btrim(recipient_name) <> '' AND recipient_phone IS NOT NULL AND btrim(recipient_phone) <> '' AND delivery_address_snapshot IS NOT NULL AND btrim(delivery_address_snapshot) <> '')",
        ),
        _ck(
            __tablename__,
            "completion_marker",
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)",
        ),
        _ck(
            __tablename__,
            "cancellation_marker",
            "(status = 'CANCELLED') = (cancelled_at IS NOT NULL)",
        ),
        comment="Sales order ends at complete verified Warehouse carrier handoff, not customer delivery. Freeze confirmed snapshots/input_revision. Completion quantities and MAX handoff time require deduplicated source evidence; no whole-order cancellation after first handoff.",
    )

    order_number: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    customer_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(30), nullable=False, server_default="DRAFT")
    requested_handoff_at: Mapped[datetime | None] = _time(
        comment="Original immutable deadline for full carrier handoff after confirmation; not customer delivery time."
    )
    recipient_name: Mapped[str | None] = mapped_column(sa.String(200))
    recipient_phone: Mapped[str | None] = mapped_column(sa.String(40))
    delivery_address_snapshot: Mapped[str | None] = mapped_column(sa.Text)
    delivery_instructions: Mapped[str | None] = mapped_column(sa.Text)
    currency: Mapped[str] = mapped_column(sa.CHAR(3), nullable=False, server_default="VND")
    fulfillment_warehouse_ref: Mapped[UUID | None] = mapped_column(
        sa.Uuid,
        comment="Opaque Warehouse ID; same-org verification is an authorized interface check.",
    )
    input_revision: Mapped[int] = _version()
    workflow_instance_ref: Mapped[str | None] = mapped_column(sa.String(128))
    version: Mapped[int] = _version()
    created_by_subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    confirmed_at: Mapped[datetime | None] = _time()
    completed_at: Mapped[datetime | None] = _time(
        comment="Latest verified physical Warehouse handoff when all original lines are fulfilled."
    )
    cancelled_at: Mapped[datetime | None] = _time()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class SalesOrderLine(_OrgRow, SalesBase):
    __tablename__ = "sales_order_lines"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "order_id", "line_no"),
        _uq(__tablename__, "org_id", "order_id", "id"),
        _fk(__tablename__, ("org_id", "order_id"), "sales_orders", ("org_id", "id")),
        _ix(__tablename__, "org_id", "sku"),
        _ck(
            __tablename__,
            "quantities",
            "line_no > 0 AND ordered_quantity > 0 AND unit_price >= 0",
        ),
        _nonblank(__tablename__, "sku", "product_name_snapshot", "uom"),
        comment="Frozen original Sales line after confirmation. Product UUID is opaque; approved Warehouse SKU/UOM must match. Currency inherited from order; do not sum different UOMs.",
    )

    order_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    line_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    product_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    sku: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    uom: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    ordered_quantity: Mapped[Decimal] = _quantity()
    unit_price: Mapped[Decimal] = mapped_column(sa.Numeric(18, 2), nullable=False)
    created_at: Mapped[datetime] = _created()


class SalesOrderEvent(_OrgRow, SalesBase):
    __tablename__ = "sales_order_events"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "source_app", "source_event_id"),
        _fk(__tablename__, ("org_id", "order_id"), "sales_orders", ("org_id", "id")),
        _ix(__tablename__, "org_id", "order_id", "occurred_at"),
        _ck(__tablename__, "from_status", f"from_status IN ({SALES_STATES})"),
        _ck(__tablename__, "to_status", f"to_status IN ({SALES_STATES})"),
        _ck(__tablename__, "source_version", "source_aggregate_version > 0"),
        _ck(
            __tablename__,
            "external_source",
            "source_app = 'sales' OR source_event_id IS NOT NULL",
        ),
        _nonblank(__tablename__, "event_type", "source_app"),
        comment="Append-only business evidence, not Flowable history. Local source_app='sales'; imported events require source_event_id and an authorized inbox receipt in the same transaction. Preserve handoff line deltas and reconcile out-of-order events.",
    )

    order_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    from_status: Mapped[str | None] = mapped_column(sa.String(30))
    to_status: Mapped[str | None] = mapped_column(sa.String(30))
    source_app: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    source_event_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    source_aggregate_version: Mapped[int | None] = mapped_column(sa.BigInteger)
    actor_subject: Mapped[str | None] = mapped_column(sa.String(255))
    correlation_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        comment="Minimal verified business evidence; no credentials/unapproved remote data.",
    )
    occurred_at: Mapped[datetime] = _time(nullable=False)
    recorded_at: Mapped[datetime] = _created()


class SalesCommandReceipt(_CommandReceipt, SalesBase):
    __tablename__ = "command_receipts"


class SalesOutboxEvent(_OutboxEvent, SalesBase):
    __tablename__ = "outbox_events"


class SalesInboxReceipt(_InboxReceipt, SalesBase):
    __tablename__ = "inbox_receipts"
