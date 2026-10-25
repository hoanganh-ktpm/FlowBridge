# SPDX-License-Identifier: GPL-3.0-or-later
"""Technical scoped-command primitives; domain services own their tables and rules."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi.encoders import jsonable_encoder
from pydantic import Field, field_validator, model_validator

from app.platform.business import StrictBody
from app.platform.http.errors import CoreError, ErrorCode


class WarehouseScope(StrictBody):
    org_id: UUID
    warehouse_id: UUID


class EventQuery(WarehouseScope):
    since: datetime
    after_time: datetime | None = None
    after_id: UUID | None = None
    limit: int = Field(default=100, ge=1, le=200, strict=True)

    @model_validator(mode="after")
    def cursor(self):
        if (self.after_time is None) != (self.after_id is None):
            raise ValueError("Both event cursor fields are required")
        if any(
            value is not None and value.utcoffset() is None
            for value in (self.since, self.after_time)
        ):
            raise ValueError("Event timestamps require timezone")
        if self.after_time is not None and self.after_time < self.since:
            raise ValueError("Cursor precedes replay window")
        return self


def event_feed(runtime, table, actor, body):
    """Authorized summaries of native events; no receiver names or contact data.

    The caller may replay from the beginning to catch late commits. A keyset
    cursor is a bounded paging aid, never a guarantee that no older event can
    still commit. Source reads remain mandatory regardless of this feed.
    """
    allow_scope(runtime, actor, body.org_id, body.warehouse_id, "operations.events.read")
    query = sa.select(table).where(
        table.c.org_id == body.org_id,
        table.c.payload["warehouse_id"].astext == str(body.warehouse_id),
        table.c.event_type.like(runtime.domain + ".%"),
        table.c.occurred_at >= body.since,
    )
    if body.after_id is not None:
        query = query.where(
            sa.tuple_(table.c.occurred_at, table.c.id) > sa.tuple_(body.after_time, body.after_id)
        )
    with runtime.engine.connect() as conn:
        rows = (
            conn.execute(query.order_by(table.c.occurred_at, table.c.id).limit(body.limit + 1))
            .mappings()
            .all()
        )
    items = []
    for row in rows[: body.limit]:
        if row["payload_hash"] != canonical(row["payload"]):
            raise CoreError(ErrorCode.INVALID_STATE)
        summary = jsonable_encoder(
            {
                "id": row["id"],
                "org_id": row["org_id"],
                "warehouse_id": body.warehouse_id,
                "producer": runtime.domain,
                "event_type": row["event_type"],
                "schema_version": row["event_schema_version"],
                "aggregate_type": row["aggregate_type"],
                "aggregate_id": row["aggregate_id"],
                "aggregate_version": row["aggregate_version"],
                "correlation_id": row["correlation_id"],
                "occurred_at": row["occurred_at"],
                "source_payload_hash": row["payload_hash"],
            }
        )
        summary["summary_hash"] = canonical(summary)
        items.append(summary)
    return {"items": items, "has_more": len(rows) > body.limit}


def install_event_feed(app, runtime, tables, actor):
    @app.post("/api/v1/" + runtime.domain + "/operations/events/read")
    def read(body: EventQuery, identity=actor):
        return event_feed(runtime, tables["outbox_events"], identity, body)


class CommandReceiptQuery(WarehouseScope):
    operation: Literal[
        "operations.preparation.confirm",
        "operations.inbound.update_eta",
        "operations.receipts.post",
        "operations.stock.reserve",
        "operations.dispatch.prepare",
        "operations.dispatch.handoff",
        "operations.shipments.book",
        "operations.shipments.reconcile",
        "operations.orders.reconcile",
    ]
    idempotency_key: str = Field(min_length=1, max_length=128)


def command_receipt(runtime, actor, body):
    allow_scope(runtime, actor, body.org_id, body.warehouse_id, body.operation)
    table = runtime.receipts
    with runtime.engine.connect() as conn:
        row = (
            conn.execute(
                sa.select(table).where(
                    table.c.org_id == body.org_id,
                    table.c.scope == f"workflow:{runtime.domain}:{body.operation}",
                    table.c.idempotency_key == body.idempotency_key,
                    table.c.resource_type == "warehouse_scope",
                    table.c.resource_id == body.warehouse_id,
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return {"state": "UNCONFIRMED", "outcome_certain": False, "resource_refs": {}}

    def references(value):
        result = {
            key: value[key]
            for key in (
                "id",
                "receipt_id",
                "status",
                "version",
                "manifest_hash",
                "acceptance_ref",
                "handoff_event_id",
            )
            if key in value
        }
        for key in ("dispatch", "reservation"):
            if isinstance(value.get(key), dict):
                result[key] = references(value[key])
        return result

    return {
        "state": row["status"],
        "outcome_certain": row["status"] in {"SUCCEEDED", "FAILED"},
        "resource_refs": references(row["response_body"] or {}),
    }


class PreparationCommand(WarehouseScope):
    preparation_minutes: int = Field(ge=0, le=10080, strict=True)
    expected_revision: int = Field(ge=0, strict=True)


class InboundRef(WarehouseScope):
    inbound_plan_id: UUID
    expected_updated_at: datetime

    @field_validator("expected_updated_at")
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value


class EtaCommand(InboundRef):
    expected_at: datetime

    @field_validator("expected_at")
    @classmethod
    def aware_eta(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value


def allow_scope(runtime, actor, org_id, warehouse_id, permission):
    runtime.allow(actor, org_id, permission)
    binding = runtime.settings.actor_bindings.get(actor.subject, {})
    if str(warehouse_id) not in binding.get("warehouse_ids", []):
        raise CoreError(ErrorCode.FORBIDDEN)


def lock_scope(conn, *parts):
    conn.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
        {"scope": ":".join(map(str, parts))},
    )


def canonical(value):
    return hashlib.sha256(
        json.dumps(
            jsonable_encoder(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def emit(
    conn,
    table,
    *,
    org_id,
    kind,
    aggregate,
    identifier,
    version,
    correlation,
    payload,
    now,
    business_key=None,
):
    event_id = uuid4()
    payload = {**jsonable_encoder(payload), "org_id": str(org_id), "source_event_id": str(event_id)}
    conn.execute(
        table.insert().values(
            id=event_id,
            org_id=org_id,
            event_type=kind,
            event_schema_version=1,
            aggregate_type=aggregate,
            aggregate_id=identifier,
            aggregate_version=version,
            business_key=business_key or str(identifier),
            correlation_id=correlation,
            payload=payload,
            payload_hash=canonical(payload),
            status="PENDING",
            attempt_count=0,
            occurred_at=now,
        )
    )
    return event_id


def verified_event_shape(event, org_id):
    if (
        str(event["org_id"]) != str(org_id)
        or event["producer_app"] != "warehouse"
        or event["event_type"] != "warehouse.dispatch_handed_over"
        or event["aggregate_type"] != "dispatch"
        or event["payload_hash"] != canonical(event["payload"])
    ):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    payload = event["payload"]
    if (
        type(event["event_schema_version"]) is not int
        or event["event_schema_version"] != 1
        or type(event["aggregate_version"]) is not int
        or event["aggregate_version"] < 1
        or payload.get("org_id") != str(org_id)
        or payload.get("source_event_id") != str(event["id"])
        or payload.get("dispatch_id") != str(event["aggregate_id"])
    ):
        raise CoreError(ErrorCode.VALIDATION_ERROR)


def receive_verified_event(conn, table, event, org_id, consumer, now):
    """Only after an authenticated owner API read, never on raw browser JSON."""
    verified_event_shape(event, org_id)
    lock_scope(conn, "inbox", org_id, event["id"], consumer)
    previous = (
        conn.execute(
            sa.select(table)
            .where(
                table.c.org_id == org_id,
                table.c.producer_app == "warehouse",
                table.c.event_id == UUID(str(event["id"])),
                table.c.consumer == consumer,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if previous:
        if (
            previous["payload_hash"] != event["payload_hash"]
            or previous["aggregate_id"] != UUID(str(event["aggregate_id"]))
            or previous["aggregate_version"] != event["aggregate_version"]
        ):
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        return previous["id"], previous["status"] == "PROCESSED"
    identifier = uuid4()
    conn.execute(
        table.insert().values(
            id=identifier,
            org_id=org_id,
            producer_app="warehouse",
            event_id=UUID(str(event["id"])),
            event_type=event["event_type"],
            event_schema_version=event["event_schema_version"],
            consumer=consumer,
            payload=event["payload"],
            payload_hash=event["payload_hash"],
            aggregate_type="dispatch",
            aggregate_id=UUID(str(event["aggregate_id"])),
            aggregate_version=event["aggregate_version"],
            status="RECEIVED",
            attempt_count=1,
            received_at=now,
        )
    )
    return identifier, False


def processed_event(conn, table, org_id, identifier, now):
    conn.execute(
        table.update()
        .where(table.c.org_id == org_id, table.c.id == identifier)
        .values(status="PROCESSED", processed_at=now, last_error_code=None)
    )
