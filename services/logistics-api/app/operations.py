# SPDX-License-Identifier: GPL-3.0-or-later
"""Native carrier slot booking; acceptance never establishes a physical handoff."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi import Depends, Header
from fastapi.encoders import jsonable_encoder
from pydantic import Field, field_validator

from app.platform.business_operations import (
    CommandReceiptQuery,
    WarehouseScope,
    allow_scope,
    canonical,
    command_receipt,
    emit,
    install_event_feed,
    lock_scope,
    processed_event,
    receive_verified_event,
    verified_event_shape,
)
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import authenticated_identity


class BookCommand(WarehouseScope):
    dispatch_id: UUID
    expected_dispatch_version: int = Field(ge=1, strict=True)
    expected_manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    carrier_code: str = Field(min_length=1, max_length=50)
    carrier_name: str = Field(min_length=1, max_length=200)
    scheduled_handoff_at: datetime
    expected_capacity_updated_at: datetime

    @field_validator("scheduled_handoff_at", "expected_capacity_updated_at")
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value

    @field_validator("carrier_code", "carrier_name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Carrier identity required")
        return value.strip()


class ReconcileCommand(WarehouseScope):
    shipment_id: UUID
    expected_version: int = Field(ge=1, strict=True)


class ReadShipment(WarehouseScope):
    shipment_id: UUID


class LogisticsOperations:
    def __init__(self, runtime, tables):
        self.runtime, self.tables = runtime, tables

    def book(self, actor, body, key, prepared, order):
        allow_scope(
            self.runtime, actor, body.org_id, body.warehouse_id, "operations.shipments.book"
        )
        manifest, dispatch = prepared["manifest"], prepared["dispatch"]
        if (
            canonical(manifest) != body.expected_manifest_hash
            or prepared["manifest_hash"] != body.expected_manifest_hash
            or str(dispatch["id"]) != str(body.dispatch_id)
            or (manifest["org_id"], manifest["warehouse_ref"], manifest["sales_order_ref"])
            != (str(body.org_id), str(body.warehouse_id), str(order["id"]))
            or (
                str(order["org_id"]),
                str(order["fulfillment_warehouse_ref"]),
                order["input_revision"],
            )
            != (str(body.org_id), str(body.warehouse_id), manifest["source_order_revision"])
        ):
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        original = {str(line["id"]): line for line in order["lines"]}
        if (
            not manifest["lines"]
            or len(manifest["lines"]) > 20
            or len({line["sales_order_line_ref"] for line in manifest["lines"]})
            != len(manifest["lines"])
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        for line in manifest["lines"]:
            source = original.get(line["sales_order_line_ref"])
            if (
                not source
                or (line["product_ref"], line["sku"], line["uom"])
                != (str(source["product_ref"]), source["sku"], source["uom"])
                or not 0 < Decimal(line["quantity"]) <= Decimal(str(source["ordered_quantity"]))
            ):
                raise CoreError(ErrorCode.VALIDATION_ERROR)

        def write(conn):
            lock_scope(conn, "shipment", body.org_id, body.dispatch_id)
            shipments = self.tables["shipments"]
            existing = (
                conn.execute(
                    sa.select(shipments)
                    .where(
                        shipments.c.org_id == body.org_id,
                        shipments.c.warehouse_dispatch_ref == body.dispatch_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if existing:
                if (
                    existing["manifest_hash"] != body.expected_manifest_hash
                    or existing["carrier_code_snapshot"] != body.carrier_code
                    or existing["scheduled_handoff_at"] != body.scheduled_handoff_at
                    or existing["status"] not in {"ACCEPTED", "HANDED_OVER"}
                ):
                    raise CoreError(ErrorCode.VERSION_CONFLICT)
                return dict(existing)
            if (
                dispatch["status"] != "PREPARED"
                or dispatch["version"] != body.expected_dispatch_version
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            now = datetime.now(UTC)
            if body.scheduled_handoff_at < now or order["status"] in {
                "CANCELLED",
                "CANCEL_PENDING",
                "COMPLETED",
            }:
                raise CoreError(ErrorCode.INVALID_STATE)
            day = body.scheduled_handoff_at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
            calendar = self.runtime.one(
                conn,
                self.tables["handoff_capacity_calendar"],
                body.org_id,
                carrier_code=body.carrier_code,
                capacity_date=day,
                lock=True,
            )
            used = conn.scalar(
                sa.select(sa.func.count())
                .select_from(shipments)
                .where(
                    shipments.c.org_id == body.org_id,
                    shipments.c.carrier_code_snapshot == body.carrier_code,
                    sa.cast(
                        sa.func.timezone("Asia/Ho_Chi_Minh", shipments.c.scheduled_handoff_at),
                        sa.Date,
                    )
                    == day,
                    shipments.c.status.in_(["ACCEPTED", "HANDED_OVER"]),
                )
            )
            if (
                calendar["updated_at"] != body.expected_capacity_updated_at
                or calendar["status"] != "ACTIVE"
                or used >= calendar["max_dispatches"]
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            identifier, acceptance = uuid4(), uuid4()
            conn.execute(
                shipments.insert().values(
                    id=identifier,
                    org_id=body.org_id,
                    shipment_number="VD-" + identifier.hex[:20],
                    sales_order_ref=UUID(str(order["id"])),
                    sales_order_number=order["order_number"],
                    warehouse_ref=body.warehouse_id,
                    warehouse_dispatch_ref=body.dispatch_id,
                    warehouse_dispatch_number=dispatch["dispatch_number"],
                    manifest_version=manifest["manifest_version"],
                    manifest_hash=body.expected_manifest_hash,
                    carrier_code_snapshot=body.carrier_code,
                    carrier_name_snapshot=body.carrier_name,
                    recipient_name_snapshot=order["recipient_name"],
                    recipient_phone_snapshot=order["recipient_phone"],
                    delivery_address_snapshot=order["delivery_address_snapshot"],
                    delivery_instructions_snapshot=order.get("delivery_instructions"),
                    scheduled_handoff_at=body.scheduled_handoff_at,
                    planned_handoff_at=datetime.fromisoformat(
                        str(order["requested_handoff_at"]).replace("Z", "+00:00")
                    ),
                    status="ACCEPTED",
                    acceptance_ref=acceptance,
                    accepted_at=now,
                    created_by_subject=actor.subject,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                self.tables["shipment_lines"].insert(),
                [
                    {
                        "id": uuid4(),
                        "org_id": body.org_id,
                        "shipment_id": identifier,
                        "line_no": i,
                        "warehouse_dispatch_line_ref": UUID(line["dispatch_line_id"]),
                        "sales_order_line_ref": UUID(line["sales_order_line_ref"]),
                        "product_ref": UUID(line["product_ref"]),
                        "sku": line["sku"],
                        "product_name_snapshot": original[line["sales_order_line_ref"]][
                            "product_name_snapshot"
                        ],
                        "uom": line["uom"],
                        "planned_quantity": Decimal(line["quantity"]),
                        "handed_over_quantity": 0,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for i, line in enumerate(manifest["lines"], 1)
                ],
            )
            event = emit(
                conn,
                self.tables["outbox_events"],
                org_id=body.org_id,
                kind="logistics.shipment_accepted",
                aggregate="shipment",
                identifier=identifier,
                version=1,
                correlation=UUID(str(order["id"])),
                business_key=order["order_number"],
                payload={
                    "order_id": str(order["id"]),
                    "warehouse_id": str(body.warehouse_id),
                    "dispatch_id": str(body.dispatch_id),
                    "shipment_id": str(identifier),
                    "acceptance_ref": str(acceptance),
                },
                now=now,
            )
            conn.execute(
                self.tables["shipment_events"]
                .insert()
                .values(
                    id=uuid4(),
                    org_id=body.org_id,
                    shipment_id=identifier,
                    event_type="shipment_accepted",
                    from_status=None,
                    to_status="ACCEPTED",
                    source_app="logistics",
                    source_event_id=event,
                    source_aggregate_version=1,
                    actor_subject=actor.subject,
                    correlation_id=UUID(str(order["id"])),
                    occurred_at=now,
                    recorded_at=now,
                )
            )
            return self.runtime.one(conn, shipments, body.org_id, id=identifier)

        return self.runtime.command(
            actor,
            body,
            "operations.shipments.book",
            key,
            lambda conn: jsonable_encoder(write(conn), custom_encoder={Decimal: str}),
            response_status=201,
            resource_type="warehouse_scope",
            resource_id=body.warehouse_id,
        )

    def reconcile(self, actor, body, key, prepared, event):
        allow_scope(
            self.runtime, actor, body.org_id, body.warehouse_id, "operations.shipments.reconcile"
        )
        dispatch = prepared["dispatch"]
        verified_event_shape(event, body.org_id)
        if (
            dispatch["status"] != "HANDED_OVER"
            or str(dispatch["handoff_event_id"]) != str(event["id"])
            or str(event["aggregate_id"]) != str(dispatch["id"])
        ):
            raise CoreError(ErrorCode.INVALID_STATE)

        def write(conn):
            table = self.tables["shipments"]
            row = self.runtime.one(
                conn,
                table,
                body.org_id,
                id=body.shipment_id,
                warehouse_ref=body.warehouse_id,
                lock=True,
            )
            if (
                str(row["warehouse_dispatch_ref"]) != str(dispatch["id"])
                or str(dispatch["logistics_shipment_ref"]) != str(row["id"])
                or str(dispatch["logistics_acceptance_ref"]) != str(row["acceptance_ref"])
                or row["manifest_hash"] != prepared["manifest_hash"]
                or prepared["manifest_hash"] != canonical(prepared["manifest"])
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            if row["status"] == "HANDED_OVER":
                if str(row["handed_over_event_ref"]) != str(event["id"]):
                    raise CoreError(ErrorCode.VERSION_CONFLICT)
                return row
            now = datetime.now(UTC)
            at = datetime.fromisoformat(str(dispatch["handed_over_at"]).replace("Z", "+00:00"))
            if (
                row["version"] != body.expected_version
                or row["status"] != "ACCEPTED"
                or at > now
                or at < row["accepted_at"]
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            inbox, duplicate = receive_verified_event(
                conn, self.tables["inbox_receipts"], event, body.org_id, "logistics-handoff", now
            )
            if duplicate:
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            conn.execute(
                table.update()
                .where(table.c.org_id == body.org_id, table.c.id == row["id"])
                .values(
                    status="HANDED_OVER",
                    handed_over_event_ref=UUID(str(event["id"])),
                    handed_over_source_version=event["aggregate_version"],
                    handed_over_at=at,
                    version=table.c.version + 1,
                    updated_at=now,
                )
            )
            lines = self.tables["shipment_lines"]
            conn.execute(
                lines.update()
                .where(lines.c.org_id == body.org_id, lines.c.shipment_id == row["id"])
                .values(handed_over_quantity=lines.c.planned_quantity, updated_at=now)
            )
            conn.execute(
                self.tables["shipment_events"]
                .insert()
                .values(
                    id=uuid4(),
                    org_id=body.org_id,
                    shipment_id=row["id"],
                    event_type="warehouse_handoff_verified",
                    from_status="ACCEPTED",
                    to_status="HANDED_OVER",
                    source_app="warehouse",
                    source_event_id=UUID(str(event["id"])),
                    source_aggregate_version=event["aggregate_version"],
                    actor_subject=actor.subject,
                    correlation_id=row["sales_order_ref"],
                    occurred_at=at,
                    recorded_at=now,
                )
            )
            emit(
                conn,
                self.tables["outbox_events"],
                org_id=body.org_id,
                kind="logistics.handoff_verified",
                aggregate="shipment",
                identifier=row["id"],
                version=row["version"] + 1,
                correlation=row["sales_order_ref"],
                business_key=row["sales_order_number"],
                payload={
                    "order_id": str(row["sales_order_ref"]),
                    "warehouse_id": str(body.warehouse_id),
                    "warehouse_handoff_event_id": str(event["id"]),
                    "handed_over_at": at,
                },
                now=now,
            )
            processed_event(conn, self.tables["inbox_receipts"], body.org_id, inbox, now)
            return self.runtime.one(conn, table, body.org_id, id=row["id"])

        return self.runtime.command(
            actor,
            body,
            "operations.shipments.reconcile",
            key,
            lambda conn: jsonable_encoder(write(conn), custom_encoder={Decimal: str}),
            resource_type="warehouse_scope",
            resource_id=body.warehouse_id,
        )


def install(app, runtime, tables):
    operations = LogisticsOperations(runtime, tables)
    actor = Depends(authenticated_identity)
    install_event_feed(app, runtime, tables, actor)

    @app.post("/api/v1/logistics/operations/commands/read")
    def receipt_status(body: CommandReceiptQuery, identity=actor):
        return command_receipt(runtime, identity, body)

    @app.post("/api/v1/logistics/operations/shipments/read")
    def read_shipment(body: ReadShipment, identity=actor):
        return shipment(body.shipment_id, body.org_id, body.warehouse_id, identity)

    @app.post("/api/v1/logistics/operations/capacity/read")
    def read_capacity(body: WarehouseScope, identity=actor):
        return capacity(body.org_id, body.warehouse_id, identity)

    @app.post("/api/v1/logistics/operations/shipments/reconcile")
    def reconcile(
        body: ReconcileCommand,
        identity=actor,
        authorization: str = Header(),
        idempotency_key: str = Header(default=""),
    ):
        allow_scope(
            runtime, identity, body.org_id, body.warehouse_id, "operations.shipments.reconcile"
        )
        current = runtime.fetch(
            tables["shipments"], body.org_id, id=body.shipment_id, warehouse_ref=body.warehouse_id
        )
        token = authorization.partition(" ")[2]
        path = f"/api/v1/warehouse/operations/dispatches/{current['warehouse_dispatch_ref']}"
        query = f"?org_id={body.org_id}&warehouse_id={body.warehouse_id}"
        prepared = runtime.remote("warehouse", "GET", path + query, token)
        event = runtime.remote("warehouse", "GET", path + "/handoff-event" + query, token)
        return operations.reconcile(identity, body, idempotency_key, prepared, event)

    @app.get("/api/v1/logistics/operations/capacity")
    def capacity(org_id: UUID, warehouse_id: UUID, identity=actor):
        allow_scope(runtime, identity, org_id, warehouse_id, "operations.capacity.read")
        with runtime.engine.connect() as conn:
            return {
                "items": [
                    dict(row)
                    for row in conn.execute(
                        sa.select(tables["handoff_capacity_calendar"]).where(
                            tables["handoff_capacity_calendar"].c.org_id == org_id
                        )
                    ).mappings()
                ]
            }

    @app.get("/api/v1/logistics/operations/shipments/{shipment_id}")
    def shipment(shipment_id: UUID, org_id: UUID, warehouse_id: UUID, identity=actor):
        allow_scope(runtime, identity, org_id, warehouse_id, "operations.shipments.read")
        return jsonable_encoder(
            runtime.fetch(tables["shipments"], org_id, id=shipment_id, warehouse_ref=warehouse_id),
            custom_encoder={Decimal: str},
        )

    @app.post("/api/v1/logistics/operations/shipments", status_code=201)
    def book(
        body: BookCommand,
        identity=actor,
        authorization: str = Header(),
        idempotency_key: str = Header(default=""),
    ):
        allow_scope(runtime, identity, body.org_id, body.warehouse_id, "operations.shipments.book")
        token = authorization.partition(" ")[2]
        prepared = runtime.remote(
            "warehouse",
            "GET",
            f"/api/v1/warehouse/operations/dispatches/{body.dispatch_id}?org_id={body.org_id}&warehouse_id={body.warehouse_id}",
            token,
        )
        order = runtime.remote(
            "sales",
            "GET",
            f"/api/v1/sales/operations/orders/{UUID(prepared['manifest']['sales_order_ref'])}?org_id={body.org_id}&warehouse_id={body.warehouse_id}",
            token,
        )
        return operations.book(identity, body, idempotency_key, prepared, order)
