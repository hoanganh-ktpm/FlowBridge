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
from datetime import date, datetime
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


class LogisticsBase(DeclarativeBase):
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


class WorkflowShipment(_OrgRow, LogisticsBase):
    __tablename__ = "wf_e2e_shipments"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "sales_order_ref"),
        _uq(__tablename__, "org_id", "warehouse_dispatch_ref"),
        _ck(__tablename__, "status", "status IN ('CREATED','HANDED_OVER','DELIVERED')"),
        _ck(__tablename__, "delivery", "(status = 'DELIVERED') = (delivered_at IS NOT NULL)"),
        _ck(__tablename__, "handoff", "status = 'CREATED' OR (handed_over_at IS NOT NULL AND warehouse_handoff_event_ref IS NOT NULL)"),
        _ck(__tablename__, "chronology", "delivered_at IS NULL OR delivered_at >= handed_over_at"),
        comment="Isolated delivery extension for workflow E2E; historical shipments retain their physical carrier-handoff semantics.",
    )
    sales_order_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    warehouse_dispatch_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    warehouse_handoff_event_ref: Mapped[UUID | None] = mapped_column(sa.Uuid)
    handed_over_at: Mapped[datetime | None] = _time()
    delivered_at: Mapped[datetime | None] = _time()
    event_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    version: Mapped[int] = _version()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class HandoffCapacityCalendar(_OrgRow, LogisticsBase):
    __tablename__ = "handoff_capacity_calendar"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "carrier_code", "capacity_date"),
        _ck(__tablename__, "capacity", "max_dispatches >= 0"),
        _ck(__tablename__, "status", "status IN ('ACTIVE', 'UNAVAILABLE')"),
        _nonblank(__tablename__, "carrier_code"),
        comment="Local carrier dispatch slots by Asia/Ho_Chi_Minh date. Booking requires a locked calendar row; historical handoffs consume capacity.",
    )
    carrier_code: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    capacity_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    max_dispatches: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class Shipment(_OrgRow, LogisticsBase):
    __tablename__ = "shipments"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "shipment_number"),
        _uq(__tablename__, "org_id", "warehouse_dispatch_ref"),
        _uq(__tablename__, "org_id", "acceptance_ref"),
        _uq(__tablename__, "org_id", "handed_over_event_ref"),
        _ix(__tablename__, "org_id", "sales_order_ref", "status"),
        _ck(__tablename__, "status", f"status IN ({SHIPMENT_STATES})"),
        _ck(
            __tablename__,
            "versions",
            "manifest_version > 0 AND version > 0 AND (handed_over_source_version IS NULL OR handed_over_source_version > 0)",
        ),
        _hash_check(__tablename__, "manifest_hash"),
        _nonblank(
            __tablename__,
            "shipment_number",
            "sales_order_number",
            "warehouse_dispatch_number",
            "carrier_code_snapshot",
            "carrier_name_snapshot",
            "recipient_name_snapshot",
            "recipient_phone_snapshot",
            "delivery_address_snapshot",
            "created_by_subject",
        ),
        _ck(
            __tablename__,
            "acceptance_evidence",
            "status NOT IN ('ACCEPTED', 'HANDED_OVER') OR (acceptance_ref IS NOT NULL AND accepted_at IS NOT NULL)",
        ),
        _ck(
            __tablename__,
            "handoff_evidence",
            "status <> 'HANDED_OVER' OR (handed_over_event_ref IS NOT NULL AND handed_over_source_version IS NOT NULL AND handed_over_at IS NOT NULL AND accepted_at IS NOT NULL)",
        ),
        _ck(
            __tablename__,
            "handoff_marker",
            "(status = 'HANDED_OVER') = (handed_over_at IS NOT NULL)",
        ),
        _ck(
            __tablename__,
            "cancelled_marker",
            "(status = 'CANCELLED') = (cancelled_at IS NOT NULL)",
        ),
        _ck(
            __tablename__,
            "handoff_time",
            "handed_over_at IS NULL OR handed_over_at >= accepted_at",
        ),
        comment="One frozen Warehouse dispatch per shipment. Acceptance is planning; only verified deduplicated same-org Warehouse evidence enables handoff projection. Recipient fields are private authorized shipping labels, not customer receipt evidence. Serialize prepare/cancel on opaque dispatch ID and preserve inbox cancellation fence.",
    )

    shipment_number: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    sales_order_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    sales_order_number: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    warehouse_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    warehouse_dispatch_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    warehouse_dispatch_number: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    manifest_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    carrier_code_snapshot: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    carrier_name_snapshot: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    tracking_number: Mapped[str | None] = mapped_column(
        sa.String(100),
        comment="Carrier label reference, not customer delivery evidence.",
    )
    recipient_name_snapshot: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    recipient_phone_snapshot: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    delivery_address_snapshot: Mapped[str] = mapped_column(sa.Text, nullable=False)
    delivery_instructions_snapshot: Mapped[str | None] = mapped_column(sa.Text)
    scheduled_handoff_at: Mapped[datetime | None] = _time()
    planned_handoff_at: Mapped[datetime] = _time(
        nullable=False,
        comment="Immutable accepted Sales original carrier-handoff deadline; not customer delivery promise.",
    )
    status: Mapped[str] = mapped_column(sa.String(30), nullable=False, server_default="DRAFT")
    acceptance_ref: Mapped[UUID | None] = mapped_column(sa.Uuid)
    handed_over_event_ref: Mapped[UUID | None] = mapped_column(sa.Uuid)
    handed_over_source_version: Mapped[int | None] = mapped_column(sa.BigInteger)
    accepted_at: Mapped[datetime | None] = _time()
    handed_over_at: Mapped[datetime | None] = _time()
    cancelled_at: Mapped[datetime | None] = _time()
    workflow_instance_ref: Mapped[str | None] = mapped_column(sa.String(128))
    version: Mapped[int] = _version()
    created_by_subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class ShipmentLine(_OrgRow, LogisticsBase):
    __tablename__ = "shipment_lines"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "shipment_id", "id"),
        _uq(__tablename__, "org_id", "shipment_id", "line_no"),
        _uq(__tablename__, "org_id", "warehouse_dispatch_line_ref"),
        _fk(__tablename__, ("org_id", "shipment_id"), "shipments", ("org_id", "id")),
        _ck(
            __tablename__,
            "quantities",
            "line_no > 0 AND planned_quantity > 0 AND handed_over_quantity >= 0 AND handed_over_quantity <= planned_quantity",
        ),
        _nonblank(__tablename__, "sku", "product_name_snapshot", "uom"),
        comment="Frozen manifest preserves each original Sales line even for duplicate SKU. Handoff quantity is zero before verified Warehouse evidence, then planned quantity; enforce whole-shipment/line updates with inbox/outbox atomically. No independently editable physical counter.",
    )

    shipment_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    line_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    warehouse_dispatch_line_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    sales_order_line_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    product_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    sku: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    uom: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    planned_quantity: Mapped[Decimal] = _quantity()
    handed_over_quantity: Mapped[Decimal] = _quantity("0")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class ShipmentEvent(_OrgRow, LogisticsBase):
    __tablename__ = "shipment_events"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "source_app", "source_event_id"),
        _fk(__tablename__, ("org_id", "shipment_id"), "shipments", ("org_id", "id")),
        _ix(__tablename__, "org_id", "shipment_id", "occurred_at"),
        _ck(__tablename__, "from_status", f"from_status IN ({SHIPMENT_STATES})"),
        _ck(__tablename__, "to_status", f"to_status IN ({SHIPMENT_STATES})"),
        _ck(__tablename__, "source_version", "source_aggregate_version > 0"),
        _ck(
            __tablename__,
            "external_source",
            "source_app = 'logistics' OR source_event_id IS NOT NULL",
        ),
        _nonblank(__tablename__, "event_type", "source_app"),
        comment="Append-only business evidence, not Flowable history. Local source_app='logistics'; external handoff/cancel requires source event and validated inbox. Chat/status text is never physical handoff evidence; defer gaps rather than losing quantity deltas.",
    )

    shipment_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    from_status: Mapped[str | None] = mapped_column(sa.String(30))
    to_status: Mapped[str | None] = mapped_column(sa.String(30))
    source_app: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    source_event_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    source_aggregate_version: Mapped[int | None] = mapped_column(sa.BigInteger)
    actor_subject: Mapped[str | None] = mapped_column(sa.String(255))
    details: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        comment="Minimal auditable references, without unapproved PII propagation.",
    )
    correlation_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    occurred_at: Mapped[datetime] = _time(nullable=False)
    recorded_at: Mapped[datetime] = _created()


class LogisticsCommandReceipt(_CommandReceipt, LogisticsBase):
    __tablename__ = "command_receipts"


class LogisticsOutboxEvent(_OutboxEvent, LogisticsBase):
    __tablename__ = "outbox_events"


class LogisticsInboxReceipt(_InboxReceipt, LogisticsBase):
    __tablename__ = "inbox_receipts"
