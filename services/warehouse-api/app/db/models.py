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


class WarehouseBase(DeclarativeBase):
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


class Product(_OrgRow, WarehouseBase):
    __tablename__ = "products"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "sku"),
        _ck(__tablename__, "status", "status IN ('ACTIVE', 'INACTIVE')"),
        _ck(__tablename__, "cost", "unit_cost >= 0"),
        _ck(__tablename__, "currency", "currency ~ '^[A-Z]{3}$'"),
        _nonblank(__tablename__, "sku", "name", "uom"),
        _ck(__tablename__, "sku_trimmed", "sku = btrim(sku)"),
        comment="Authoritative Warehouse SKU/UOM catalog. unit_cost is private and MUST be excluded from Sales stock-availability contracts. Inactivate rather than reinterpreting referenced product identities.",
    )

    sku: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    uom: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(18, 2),
        comment="Private Warehouse cost; never in Sales stock-availability contract.",
    )
    currency: Mapped[str] = mapped_column(sa.CHAR(3), nullable=False, server_default="VND")
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, server_default="ACTIVE")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class Warehouse(_OrgRow, WarehouseBase):
    __tablename__ = "warehouses"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "warehouse_code"),
        _ck(__tablename__, "status", "status IN ('ACTIVE', 'INACTIVE')"),
        _nonblank(__tablename__, "warehouse_code", "name"),
        comment="One selected warehouse per Sales order in the MVP. Transfers and multi-warehouse optimization are outside scope.",
    )

    warehouse_code: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, server_default="ACTIVE")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class InventoryBalance(_OrgRow, WarehouseBase):
    __tablename__ = "inventory_balances"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "warehouse_id", "product_id"),
        _fk(__tablename__, ("org_id", "warehouse_id"), "warehouses", ("org_id", "id")),
        _fk(__tablename__, ("org_id", "product_id"), "products", ("org_id", "id")),
        _ck(
            __tablename__,
            "quantities",
            "on_hand_quantity >= reserved_quantity AND reserved_quantity >= 0",
        ),
        _ck(__tablename__, "version", "version > 0"),
        comment="available = on_hand - reserved, exposed via approved view. Reserved must equal remaining reservation holds; on_hand must equal ledger SUM. Cross-row equality needs locked service transactions, not direct UI/Agent writes.",
    )

    warehouse_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    on_hand_quantity: Mapped[Decimal] = _quantity("0")
    reserved_quantity: Mapped[Decimal] = _quantity("0")
    version: Mapped[int] = _version()
    updated_at: Mapped[datetime] = _created()


class GoodsReceipt(_OrgRow, WarehouseBase):
    __tablename__ = "goods_receipts"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "receipt_number"),
        _fk(__tablename__, ("org_id", "warehouse_id"), "warehouses", ("org_id", "id")),
        _ix(__tablename__, "org_id", "warehouse_id", "received_at"),
        _ck(__tablename__, "status", "status IN ('DRAFT', 'POSTED', 'CANCELLED')"),
        _ck(
            __tablename__,
            "source_type",
            "source_type IN ('OPENING_BALANCE', 'REPLENISHMENT')",
        ),
        _ck(__tablename__, "version", "version > 0"),
        _ck(
            __tablename__,
            "posted_marker",
            "(status = 'POSTED') = (posted_at IS NOT NULL)",
        ),
        _ck(
            __tablename__,
            "posted_time",
            "posted_at IS NULL OR posted_at >= received_at",
        ),
        _nonblank(__tablename__, "receipt_number", "received_by_subject"),
        comment="Physical receiving. Posting lines, balances, unique positive movements, command receipt and outbox is one local transaction. Includes all opening stock. Posted facts immutable; cancellation only while draft; customer returns outside MVP.",
    )

    warehouse_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    receipt_number: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    source_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    source_document_ref: Mapped[str | None] = mapped_column(sa.String(150))
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, server_default="DRAFT")
    received_at: Mapped[datetime] = _time(nullable=False)
    posted_at: Mapped[datetime | None] = _time()
    received_by_subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    notes: Mapped[str | None] = mapped_column(sa.Text)
    version: Mapped[int] = _version()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class GoodsReceiptLine(_OrgRow, WarehouseBase):
    __tablename__ = "goods_receipt_lines"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "receipt_id", "line_no"),
        _fk(__tablename__, ("org_id", "receipt_id"), "goods_receipts", ("org_id", "id")),
        _fk(__tablename__, ("org_id", "product_id"), "products", ("org_id", "id")),
        _ck(
            __tablename__,
            "quantities",
            "line_no > 0 AND received_quantity > 0 AND accepted_quantity >= 0 AND accepted_quantity <= received_quantity",
        ),
        _ck(__tablename__, "cost", "unit_cost_snapshot >= 0"),
        comment="Only accepted quantity increases usable stock. Product UOM applies; reject amount = received - accepted. Freeze lines once receipt posted. Cost is private Warehouse data.",
    )

    receipt_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    line_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    received_quantity: Mapped[Decimal] = _quantity()
    accepted_quantity: Mapped[Decimal] = _quantity()
    unit_cost_snapshot: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(18, 2), comment="Private receipt cost in product currency."
    )
    rejection_reason: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _created()


class Reservation(_OrgRow, WarehouseBase):
    __tablename__ = "reservations"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "reservation_number"),
        _uq(__tablename__, "org_id", "sales_order_ref", "source_order_revision"),
        _fk(__tablename__, ("org_id", "warehouse_id"), "warehouses", ("org_id", "id")),
        _ix(__tablename__, "org_id", "sales_order_ref", "status"),
        sa.Index(
            "uq_reservations_active_sales_order",
            "org_id",
            "sales_order_ref",
            unique=True,
            postgresql_where=sa.text("status IN ('HELD', 'PARTIALLY_DISPATCHED')"),
        ),
        _ck(
            __tablename__,
            "status",
            "status IN ('HELD', 'PARTIALLY_DISPATCHED', 'DISPATCHED', 'RELEASED', 'EXPIRED', 'CANCELLED')",
        ),
        _ck(__tablename__, "versions", "version > 0 AND source_order_revision > 0"),
        _ck(
            __tablename__,
            "cancelled_marker",
            "(status = 'CANCELLED') = (cancelled_at IS NOT NULL)",
        ),
        _nonblank(
            __tablename__,
            "reservation_number",
            "sales_order_number",
            "requested_by_subject",
        ),
        comment="All-or-nothing same-warehouse hold for frozen Sales input revision. Unique revision is also cancellation tombstone fence (zero lines). Serialize reserve/cancel before row creation; no whole-order cancel after handoff. Automatic expiry/reallocation not implemented.",
    )

    reservation_number: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    sales_order_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    sales_order_number: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    source_order_revision: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    warehouse_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(30), nullable=False, server_default="HELD")
    expires_at: Mapped[datetime | None] = _time()
    cancelled_at: Mapped[datetime | None] = _time()
    requested_by_subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    version: Mapped[int] = _version()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class ReservationLine(_OrgRow, WarehouseBase):
    __tablename__ = "reservation_lines"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "reservation_id", "id"),
        _uq(__tablename__, "org_id", "reservation_id", "sales_order_line_ref"),
        _fk(
            __tablename__,
            ("org_id", "reservation_id"),
            "reservations",
            ("org_id", "id"),
        ),
        _fk(__tablename__, ("org_id", "product_id"), "products", ("org_id", "id")),
        _ix(__tablename__, "org_id", "product_id"),
        _ck(
            __tablename__,
            "quantities",
            "requested_quantity > 0 AND allocated_quantity = requested_quantity AND released_quantity >= 0 AND dispatched_quantity >= 0 AND released_quantity + dispatched_quantity <= allocated_quantity",
        ),
        comment="Hold remaining = allocated - released - dispatched. All-or-nothing initial allocation; partial physical dispatch supported. Match opaque Sales line/product and update balance hold atomically. Cross-row sums/immutability require business service.",
    )

    reservation_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    sales_order_line_ref: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    requested_quantity: Mapped[Decimal] = _quantity()
    allocated_quantity: Mapped[Decimal] = _quantity()
    released_quantity: Mapped[Decimal] = _quantity("0")
    dispatched_quantity: Mapped[Decimal] = _quantity("0")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class Dispatch(_OrgRow, WarehouseBase):
    __tablename__ = "dispatches"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "reservation_id", "id"),
        _uq(__tablename__, "org_id", "dispatch_number"),
        _uq(__tablename__, "org_id", "logistics_shipment_ref"),
        _fk(
            __tablename__,
            ("org_id", "reservation_id"),
            "reservations",
            ("org_id", "id"),
        ),
        _ix(__tablename__, "org_id", "reservation_id", "status"),
        _ck(
            __tablename__,
            "status",
            "status IN ('DRAFT', 'PREPARED', 'HANDED_OVER', 'CANCELLED')",
        ),
        _ck(__tablename__, "versions", "version > 0 AND manifest_version > 0"),
        _hash_check(__tablename__, "manifest_hash"),
        _ck(
            __tablename__,
            "prepared_snapshot",
            "status NOT IN ('PREPARED', 'HANDED_OVER') OR (manifest_hash IS NOT NULL AND prepared_at IS NOT NULL AND prepared_by_subject IS NOT NULL AND btrim(prepared_by_subject) <> '')",
        ),
        _ck(
            __tablename__,
            "handoff_evidence",
            "status <> 'HANDED_OVER' OR (logistics_shipment_ref IS NOT NULL AND logistics_acceptance_ref IS NOT NULL AND carrier_code_snapshot IS NOT NULL AND btrim(carrier_code_snapshot) <> '' AND carrier_name_snapshot IS NOT NULL AND btrim(carrier_name_snapshot) <> '' AND carrier_receiver_name IS NOT NULL AND btrim(carrier_receiver_name) <> '' AND handoff_event_id IS NOT NULL AND handed_over_at IS NOT NULL AND handed_over_by_subject IS NOT NULL AND btrim(handed_over_by_subject) <> '')",
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
            "handed_over_at IS NULL OR handed_over_at >= prepared_at",
        ),
        _nonblank(__tablename__, "dispatch_number"),
        comment="Authoritative physical carrier handoff. PREPARED freezes nonempty canonical manifest. Handoff requires matching accepted Logistics manifest/carrier; balance/hold/ledger/evidence/outbox/command commit once atomically. Nonfuture physical time, quantity caps and immutable facts require locked service commands. No customer receipt workflow.",
    )

    dispatch_number: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    reservation_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(30), nullable=False, server_default="DRAFT")
    manifest_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    manifest_hash: Mapped[str | None] = mapped_column(sa.CHAR(64))
    logistics_shipment_ref: Mapped[UUID | None] = mapped_column(sa.Uuid)
    logistics_acceptance_ref: Mapped[UUID | None] = mapped_column(sa.Uuid)
    carrier_code_snapshot: Mapped[str | None] = mapped_column(sa.String(50))
    carrier_name_snapshot: Mapped[str | None] = mapped_column(sa.String(200))
    carrier_receiver_name: Mapped[str | None] = mapped_column(sa.String(200))
    handoff_proof_document_ref: Mapped[UUID | None] = mapped_column(
        sa.Uuid,
        comment="Opaque Core Document reference; independent document ACL still applies.",
    )
    handoff_event_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    prepared_at: Mapped[datetime | None] = _time()
    handed_over_at: Mapped[datetime | None] = _time()
    cancelled_at: Mapped[datetime | None] = _time()
    prepared_by_subject: Mapped[str | None] = mapped_column(sa.String(255))
    handed_over_by_subject: Mapped[str | None] = mapped_column(sa.String(255))
    version: Mapped[int] = _version()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class DispatchLine(_OrgRow, WarehouseBase):
    __tablename__ = "dispatch_lines"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "dispatch_id", "line_no"),
        _uq(__tablename__, "org_id", "dispatch_id", "reservation_line_id"),
        _fk(
            __tablename__,
            ("org_id", "reservation_id", "dispatch_id"),
            "dispatches",
            ("org_id", "reservation_id", "id"),
        ),
        _fk(
            __tablename__,
            ("org_id", "reservation_id", "reservation_line_id"),
            "reservation_lines",
            ("org_id", "reservation_id", "id"),
        ),
        _ck(__tablename__, "quantities", "line_no > 0 AND quantity > 0"),
        comment="Repeated reservation_id enforces same-reservation dispatch/line membership. Product/UOM derive from reservation line. Cap aggregate prepared+dispatched+released quantities while locking; freeze prepared lines.",
    )

    dispatch_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    reservation_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    reservation_line_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    line_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    quantity: Mapped[Decimal] = _quantity()
    created_at: Mapped[datetime] = _created()


class StockMovement(_OrgRow, WarehouseBase):
    __tablename__ = "stock_movements"
    __table_args__ = _args(
        __tablename__,
        _uq(__tablename__, "org_id", "goods_receipt_line_id"),
        _uq(__tablename__, "org_id", "dispatch_line_id"),
        _fk(
            __tablename__,
            ("org_id", "warehouse_id", "product_id"),
            "inventory_balances",
            ("org_id", "warehouse_id", "product_id"),
        ),
        _fk(
            __tablename__,
            ("org_id", "goods_receipt_line_id"),
            "goods_receipt_lines",
            ("org_id", "id"),
        ),
        _fk(
            __tablename__,
            ("org_id", "dispatch_line_id"),
            "dispatch_lines",
            ("org_id", "id"),
        ),
        _ix(__tablename__, "org_id", "warehouse_id", "product_id", "occurred_at"),
        _ck(
            __tablename__,
            "origin_and_sign",
            "(movement_type = 'RECEIPT' AND quantity_delta > 0 AND goods_receipt_line_id IS NOT NULL AND dispatch_line_id IS NULL) OR (movement_type = 'DISPATCH' AND quantity_delta < 0 AND dispatch_line_id IS NOT NULL AND goods_receipt_line_id IS NULL)",
        ),
        _nonblank(__tablename__, "actor_subject"),
        comment="Append-only on-hand ledger; unique origin line prevents duplicate effect. Receipt amount equals accepted quantity, dispatch magnitude equals handed-over quantity. Product/warehouse/posted-parent agreement and ledger SUM need transaction validation. Holds never create on-hand movements.",
    )

    warehouse_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    movement_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    quantity_delta: Mapped[Decimal] = _quantity()
    goods_receipt_line_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    dispatch_line_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    actor_subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    occurred_at: Mapped[datetime] = _time(nullable=False)
    recorded_at: Mapped[datetime] = _created()


class WarehouseCommandReceipt(_CommandReceipt, WarehouseBase):
    __tablename__ = "command_receipts"


class WarehouseOutboxEvent(_OutboxEvent, WarehouseBase):
    __tablename__ = "outbox_events"


class WarehouseInboxReceipt(_InboxReceipt, WarehouseBase):
    __tablename__ = "inbox_receipts"
