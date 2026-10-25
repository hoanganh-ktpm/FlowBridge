# SPDX-License-Identifier: GPL-3.0-or-later
"""Native confirmed Sales order interfaces; not the isolated wf_e2e slice."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import Depends, Header
from fastapi.encoders import jsonable_encoder
from pydantic import Field

from app.platform.business_operations import (
    CommandReceiptQuery,
    WarehouseScope,
    allow_scope,
    command_receipt,
    emit,
    processed_event,
    receive_verified_event,
)
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import authenticated_identity


class ReadOrder(WarehouseScope):
    order_id: UUID


class ReconcileOrder(ReadOrder):
    expected_version: int = Field(ge=1, strict=True)


class SalesOperations:
    def __init__(self, runtime, tables):
        self.runtime, self.tables = runtime, tables

    def reconcile(self, actor, body, key, proof, events):
        allow_scope(
            self.runtime, actor, body.org_id, body.warehouse_id, "operations.orders.reconcile"
        )
        if (
            (proof.get("org_id"), proof.get("warehouse_id"), proof.get("order_id"))
            != (str(body.org_id), str(body.warehouse_id), str(body.order_id))
            or type(proof.get("complete")) is not bool
            or not proof.get("evidence")
            or len(proof["evidence"]) > 100
        ):
            raise CoreError(ErrorCode.INVALID_STATE)
        ids = {item["source_event_id"] for item in proof["evidence"]}
        if len(ids) != len(proof["evidence"]) or {str(item["id"]) for item in events} != ids:
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        evidence = {item["source_event_id"]: item for item in proof["evidence"]}
        for event in events:
            item = evidence[str(event["id"])]
            if (
                str(event["aggregate_id"]) != item["dispatch_id"]
                or event["aggregate_version"] != item["source_version"]
                or event["payload"].get("order_id") != str(body.order_id)
                or event["payload"].get("warehouse_id") != str(body.warehouse_id)
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)

        def write(conn):
            table = self.tables["sales_orders"]
            row = self.runtime.one(
                conn,
                table,
                body.org_id,
                id=body.order_id,
                fulfillment_warehouse_ref=body.warehouse_id,
                lock=True,
            )
            lines = (
                conn.execute(
                    sa.select(self.tables["sales_order_lines"]).where(
                        self.tables["sales_order_lines"].c.org_id == body.org_id,
                        self.tables["sales_order_lines"].c.order_id == body.order_id,
                    )
                )
                .mappings()
                .all()
            )
            supplied = {line["order_line_id"]: line for line in proof["lines"]}
            if (
                row["input_revision"] != proof["source_order_revision"]
                or len(supplied) != len(proof["lines"])
                or set(supplied) != {str(line["id"]) for line in lines}
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            complete = True
            any_handed = False
            for line in lines:
                current = supplied[str(line["id"])]
                quantity = Decimal(current["handed_over_quantity"])
                if (
                    current["sku"] != line["sku"]
                    or current["uom"] != line["uom"]
                    or Decimal(current["ordered_quantity"]) != line["ordered_quantity"]
                    or not 0 <= quantity <= line["ordered_quantity"]
                ):
                    raise CoreError(ErrorCode.VERSION_CONFLICT)
                complete = complete and quantity == line["ordered_quantity"]
                any_handed = any_handed or quantity > 0
            if complete != proof["complete"] or not any_handed:
                raise CoreError(ErrorCode.INVALID_STATE)
            times = [
                datetime.fromisoformat(item["handed_over_at"].replace("Z", "+00:00"))
                for item in proof["evidence"]
            ]
            now = datetime.now(UTC)
            if any(at.utcoffset() is None or at > now for at in times):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            latest = max(times)
            if (
                complete
                and datetime.fromisoformat(proof["completed_at"].replace("Z", "+00:00")) != latest
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            target = "COMPLETED" if complete else "PARTIALLY_HANDED_OVER"
            pending = []
            for event in events:
                inbox, duplicate = receive_verified_event(
                    conn, self.tables["inbox_receipts"], event, body.org_id, "sales-handoff", now
                )
                if not duplicate:
                    pending.append((inbox, event))
            if not pending:
                if row["status"] == target and (not complete or row["completed_at"] == latest):
                    return row
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            if row["version"] != body.expected_version or row["status"] in {
                "DRAFT",
                "SUBMITTED",
                "CANCELLED",
                "COMPLETED",
            }:
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            for inbox, event in pending:
                conn.execute(
                    self.tables["sales_order_events"]
                    .insert()
                    .values(
                        id=uuid4(),
                        org_id=body.org_id,
                        order_id=body.order_id,
                        event_type="warehouse_handoff_verified",
                        from_status=row["status"],
                        to_status=target,
                        source_app="warehouse",
                        source_event_id=UUID(str(event["id"])),
                        source_aggregate_version=event["aggregate_version"],
                        actor_subject=actor.subject,
                        correlation_id=body.order_id,
                        occurred_at=now,
                        recorded_at=now,
                        details={"dispatch_id": str(event["aggregate_id"])},
                    )
                )
                processed_event(conn, self.tables["inbox_receipts"], body.org_id, inbox, now)
            conn.execute(
                table.update()
                .where(table.c.org_id == body.org_id, table.c.id == body.order_id)
                .values(
                    status=target,
                    completed_at=latest if complete else None,
                    version=table.c.version + 1,
                    updated_at=now,
                )
            )
            emit(
                conn,
                self.tables["outbox_events"],
                org_id=body.org_id,
                kind="sales.handoff_completed" if complete else "sales.handoff_partial",
                aggregate="sales_order",
                identifier=body.order_id,
                version=row["version"] + 1,
                correlation=body.order_id,
                business_key=row["order_number"],
                payload={
                    "order_id": str(body.order_id),
                    "warehouse_id": str(body.warehouse_id),
                    "complete": complete,
                    "warehouse_event_ids": sorted(ids),
                    "completed_at": latest if complete else None,
                },
                now=now,
            )
            return self.runtime.one(conn, table, body.org_id, id=body.order_id)

        return self.runtime.command(
            actor,
            body,
            "operations.orders.reconcile",
            key,
            lambda conn: jsonable_encoder(write(conn), custom_encoder={Decimal: str}),
            resource_type="warehouse_scope",
            resource_id=body.warehouse_id,
        )


def install(app, runtime, tables):
    actor = Depends(authenticated_identity)
    operations = SalesOperations(runtime, tables)

    @app.post("/api/v1/sales/operations/commands/read")
    def receipt_status(body: CommandReceiptQuery, identity=actor):
        return command_receipt(runtime, identity, body)

    @app.post("/api/v1/sales/operations/orders/read")
    def read_order(body: ReadOrder, identity=actor):
        return order(body.order_id, body.org_id, body.warehouse_id, identity)

    @app.post("/api/v1/sales/operations/orders/reconcile")
    def reconcile(
        body: ReconcileOrder,
        identity=actor,
        authorization: str = Header(),
        idempotency_key: str = Header(default=""),
    ):
        allow_scope(
            runtime, identity, body.org_id, body.warehouse_id, "operations.orders.reconcile"
        )
        token = authorization.partition(" ")[2]
        proof = runtime.remote(
            "warehouse",
            "GET",
            f"/api/v1/warehouse/operations/orders/{body.order_id}/handoff-proof?org_id={body.org_id}&warehouse_id={body.warehouse_id}",
            token,
        )
        if len(proof.get("evidence", [])) > 100:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        events = [
            runtime.remote(
                "warehouse",
                "GET",
                f"/api/v1/warehouse/operations/dispatches/{UUID(item['dispatch_id'])}/handoff-event?org_id={body.org_id}&warehouse_id={body.warehouse_id}",
                token,
            )
            for item in proof.get("evidence", [])
        ]
        return operations.reconcile(identity, body, idempotency_key, proof, events)

    @app.get("/api/v1/sales/operations/orders/{order_id}")
    def order(order_id: UUID, org_id: UUID, warehouse_id: UUID, identity=actor):
        allow_scope(runtime, identity, org_id, warehouse_id, "operations.orders.read")
        with runtime.engine.connect() as conn:
            header = runtime.one(
                conn,
                tables["sales_orders"],
                org_id,
                id=order_id,
                fulfillment_warehouse_ref=warehouse_id,
            )
            lines = (
                conn.execute(
                    sa.select(tables["sales_order_lines"])
                    .where(
                        tables["sales_order_lines"].c.org_id == org_id,
                        tables["sales_order_lines"].c.order_id == order_id,
                    )
                    .order_by(tables["sales_order_lines"].c.line_no)
                )
                .mappings()
                .all()
            )
            return jsonable_encoder(
                {**header, "lines": [dict(line) for line in lines]}, custom_encoder={Decimal: str}
            )
