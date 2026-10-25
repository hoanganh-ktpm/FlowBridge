# SPDX-License-Identifier: GPL-3.0-or-later
"""Owner commands for actual Warehouse plans, receipts, balances and ledger."""

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import Depends, Header
from fastapi.encoders import jsonable_encoder
from pydantic import Field, StrictInt, field_validator, model_validator

from app.platform.business_operations import (
    CommandReceiptQuery,
    EtaCommand,
    InboundRef,
    PreparationCommand,
    WarehouseScope,
    allow_scope,
    canonical,
    command_receipt,
    emit,
    lock_scope,
)
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import authenticated_identity


class ReceiveCommand(InboundRef):
    receipt_number: str = Field(min_length=1, max_length=50)
    received_quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    accepted_quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=3)
    received_at: datetime
    rejection_reason: str | None = Field(default=None, max_length=2000)

    @field_validator("receipt_number")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Receipt number required")
        return value.strip()

    @field_validator("received_at")
    @classmethod
    def aware_time(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value

    @model_validator(mode="after")
    def quantities(self):
        if self.accepted_quantity > self.received_quantity:
            raise ValueError("Accepted quantity cannot exceed received quantity")
        if (
            self.accepted_quantity < self.received_quantity
            and not (self.rejection_reason or "").strip()
        ):
            raise ValueError("State why received goods were rejected")
        return self


class ReserveCommand(WarehouseScope):
    order_id: UUID
    expected_order_revision: int = Field(ge=1, strict=True)
    inventory_versions: dict[UUID, StrictInt] = Field(min_length=1, max_length=20)

    @field_validator("inventory_versions")
    @classmethod
    def positive_versions(cls, value):
        if any(version < 1 for version in value.values()):
            raise ValueError("Positive inventory versions required")
        return value


class PrepareCommand(WarehouseScope):
    order_id: UUID
    expected_reservation_version: int = Field(ge=1, strict=True)


class HandoffCommand(WarehouseScope):
    dispatch_id: UUID
    shipment_id: UUID
    expected_dispatch_version: int = Field(ge=1, strict=True)
    expected_manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    carrier_receiver_name: str = Field(min_length=1, max_length=200)
    carrier_receipt_number: str = Field(min_length=1, max_length=100)
    handed_over_at: datetime

    @field_validator("handed_over_at")
    @classmethod
    def aware_handoff(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value

    @field_validator("carrier_receiver_name", "carrier_receipt_number")
    @classmethod
    def receipt_details(cls, value):
        if not value.strip():
            raise ValueError("Carrier receiver and receipt number required")
        return value.strip()


class ReadOrder(WarehouseScope):
    order_id: UUID


class ReadDispatch(WarehouseScope):
    dispatch_id: UUID


class ReadInbound(WarehouseScope):
    inbound_plan_id: UUID


class WarehouseOperations:
    def __init__(self, runtime, tables):
        self.runtime, self.tables = runtime, tables

    def scoped(self, actor, body, permission):
        allow_scope(self.runtime, actor, body.org_id, body.warehouse_id, permission)

    def command(self, actor, body, action, key, operation):
        self.scoped(actor, body, action)
        return self.runtime.command(
            actor,
            body,
            action,
            key,
            lambda conn: jsonable_encoder(operation(conn), custom_encoder={Decimal: str}),
            response_status=201
            if action in {"operations.stock.reserve", "operations.receipts.post"}
            else 200,
            resource_type="warehouse_scope",
            resource_id=body.warehouse_id,
        )

    def reservation_snapshot(self, conn, org_id, warehouse_id, order_id):
        header = self.runtime.one(
            conn,
            self.tables["reservations"],
            org_id,
            sales_order_ref=order_id,
            warehouse_id=warehouse_id,
        )
        result = {**header}
        for name, parent in (
            ("reservation_lines", "reservation_id"),
            ("dispatches", "reservation_id"),
        ):
            table = self.tables[name]
            result["lines" if name == "reservation_lines" else "dispatches"] = [
                dict(row)
                for row in conn.execute(
                    sa.select(table)
                    .where(table.c.org_id == org_id, table.c[parent] == header["id"])
                    .order_by(table.c.id)
                ).mappings()
            ]
        return result

    @staticmethod
    def order_source(body, order):
        if (
            (
                str(order.get("org_id")),
                str(order.get("fulfillment_warehouse_ref")),
                str(order.get("id")),
            )
            != (str(body.org_id), str(body.warehouse_id), str(body.order_id))
            or not order.get("confirmed_at")
            or not order.get("lines")
            or len(order["lines"]) > 20
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if len({line["id"] for line in order["lines"]}) != len(order["lines"]):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if any(
            str(line.get("org_id")) != str(body.org_id)
            or str(line.get("order_id")) != str(body.order_id)
            or Decimal(str(line["ordered_quantity"])) <= 0
            for line in order["lines"]
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)

    def reserve(self, actor, body, key, order):
        self.order_source(body, order)
        if order["input_revision"] != body.expected_order_revision:
            raise CoreError(ErrorCode.VERSION_CONFLICT)

        def write(conn):
            lock_scope(conn, "reservation", body.org_id, body.order_id)
            header_table = self.tables["reservations"]
            existing = (
                conn.execute(
                    sa.select(header_table)
                    .where(
                        header_table.c.org_id == body.org_id,
                        header_table.c.sales_order_ref == body.order_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .all()
            )
            if existing:
                if (
                    len(existing) != 1
                    or existing[0]["warehouse_id"] != body.warehouse_id
                    or existing[0]["source_order_revision"] != body.expected_order_revision
                    or existing[0]["status"] not in {"HELD", "PARTIALLY_DISPATCHED", "DISPATCHED"}
                ):
                    raise CoreError(ErrorCode.INVALID_STATE)
                return self.reservation_snapshot(
                    conn, body.org_id, body.warehouse_id, body.order_id
                )
            if order["status"] not in {"CONFIRMED", "WAITING_STOCK", "RESERVED", "FULFILLING"}:
                raise CoreError(ErrorCode.INVALID_STATE)
            needs = Counter()
            for line in order["lines"]:
                needs[UUID(str(line["product_ref"]))] += Decimal(str(line["ordered_quantity"]))
            if set(needs) != set(body.inventory_versions):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            balances = self.tables["inventory_balances"]
            stock = {
                row["product_id"]: row
                for row in conn.execute(
                    sa.select(balances)
                    .where(
                        balances.c.org_id == body.org_id,
                        balances.c.warehouse_id == body.warehouse_id,
                        balances.c.product_id.in_(needs),
                    )
                    .order_by(balances.c.product_id)
                    .with_for_update()
                ).mappings()
            }
            if set(stock) != set(needs) or any(
                stock[pid]["version"] != body.inventory_versions[pid]
                or stock[pid]["on_hand_quantity"] - stock[pid]["reserved_quantity"] < quantity
                for pid, quantity in needs.items()
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            self.runtime.one(
                conn, self.tables["warehouses"], body.org_id, id=body.warehouse_id, status="ACTIVE"
            )
            for line in order["lines"]:
                product = self.runtime.one(
                    conn,
                    self.tables["products"],
                    body.org_id,
                    id=UUID(str(line["product_ref"])),
                    status="ACTIVE",
                )
                if (product["sku"], product["uom"]) != (line["sku"], line["uom"]):
                    raise CoreError(ErrorCode.VALIDATION_ERROR)
            identifier, now = uuid4(), datetime.now(UTC)
            conn.execute(
                header_table.insert().values(
                    id=identifier,
                    org_id=body.org_id,
                    reservation_number="GH-" + identifier.hex[:20],
                    sales_order_ref=body.order_id,
                    sales_order_number=order["order_number"],
                    source_order_revision=body.expected_order_revision,
                    warehouse_id=body.warehouse_id,
                    status="HELD",
                    requested_by_subject=actor.subject,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                self.tables["reservation_lines"].insert(),
                [
                    {
                        "id": uuid4(),
                        "org_id": body.org_id,
                        "reservation_id": identifier,
                        "sales_order_line_ref": UUID(str(line["id"])),
                        "product_id": UUID(str(line["product_ref"])),
                        "requested_quantity": Decimal(str(line["ordered_quantity"])),
                        "allocated_quantity": Decimal(str(line["ordered_quantity"])),
                        "released_quantity": 0,
                        "dispatched_quantity": 0,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for line in order["lines"]
                ],
            )
            for pid, quantity in needs.items():
                conn.execute(
                    balances.update()
                    .where(balances.c.org_id == body.org_id, balances.c.id == stock[pid]["id"])
                    .values(
                        reserved_quantity=balances.c.reserved_quantity + quantity,
                        version=balances.c.version + 1,
                        updated_at=now,
                    )
                )
            emit(
                conn,
                self.tables["outbox_events"],
                org_id=body.org_id,
                kind="warehouse.order_reserved",
                aggregate="reservation",
                identifier=identifier,
                version=1,
                correlation=body.order_id,
                business_key=str(body.order_id),
                payload={
                    "warehouse_id": str(body.warehouse_id),
                    "order_id": str(body.order_id),
                    "source_order_revision": body.expected_order_revision,
                },
                now=now,
            )
            return self.reservation_snapshot(conn, body.org_id, body.warehouse_id, body.order_id)

        return self.command(actor, body, "operations.stock.reserve", key, write)

    def preparation(self, actor, body, key):
        def write(conn):
            table = self.tables["warehouses"]
            row = self.runtime.one(conn, table, body.org_id, id=body.warehouse_id, lock=True)
            if (
                row["status"] != "ACTIVE"
                or row["preparation_policy_revision"] != body.expected_revision
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            now = datetime.now(UTC)
            result = dict(
                conn.execute(
                    table.update()
                    .where(table.c.id == row["id"], table.c.org_id == body.org_id)
                    .values(
                        preparation_minutes=body.preparation_minutes,
                        preparation_policy_revision=body.expected_revision + 1,
                        preparation_confirmed_at=now,
                        preparation_confirmed_by=actor.subject,
                        updated_at=now,
                    )
                    .returning(table)
                )
                .mappings()
                .one()
            )
            emit(
                conn,
                self.tables["outbox_events"],
                org_id=body.org_id,
                kind="warehouse.preparation_policy_changed",
                aggregate="warehouse_policy",
                identifier=body.warehouse_id,
                version=body.expected_revision + 1,
                correlation=body.warehouse_id,
                payload={
                    "warehouse_id": str(body.warehouse_id),
                    "preparation_minutes": body.preparation_minutes,
                },
                now=now,
            )
            return result

        return self.command(actor, body, "operations.preparation.confirm", key, write)

    def manifest(self, conn, org_id, warehouse_id, dispatch_id):
        dispatch = self.runtime.one(conn, self.tables["dispatches"], org_id, id=dispatch_id)
        reservation = self.runtime.one(
            conn,
            self.tables["reservations"],
            org_id,
            id=dispatch["reservation_id"],
            warehouse_id=warehouse_id,
        )
        links = {
            row["id"]: row
            for row in conn.execute(
                sa.select(self.tables["reservation_lines"]).where(
                    self.tables["reservation_lines"].c.org_id == org_id,
                    self.tables["reservation_lines"].c.reservation_id == reservation["id"],
                )
            ).mappings()
        }
        lines = (
            conn.execute(
                sa.select(self.tables["dispatch_lines"])
                .where(
                    self.tables["dispatch_lines"].c.org_id == org_id,
                    self.tables["dispatch_lines"].c.dispatch_id == dispatch_id,
                )
                .order_by(self.tables["dispatch_lines"].c.line_no)
            )
            .mappings()
            .all()
        )
        values = []
        for line in lines:
            source = links[line["reservation_line_id"]]
            product = self.runtime.one(
                conn, self.tables["products"], org_id, id=source["product_id"]
            )
            values.append(
                {
                    "dispatch_line_id": str(line["id"]),
                    "reservation_line_id": str(source["id"]),
                    "sales_order_line_ref": str(source["sales_order_line_ref"]),
                    "product_ref": str(product["id"]),
                    "sku": product["sku"],
                    "uom": product["uom"],
                    "quantity": format(line["quantity"], ".3f"),
                }
            )
        content = {
            "org_id": str(org_id),
            "warehouse_ref": str(warehouse_id),
            "dispatch_id": str(dispatch_id),
            "reservation_id": str(reservation["id"]),
            "sales_order_ref": str(reservation["sales_order_ref"]),
            "source_order_revision": reservation["source_order_revision"],
            "manifest_version": dispatch["manifest_version"],
            "lines": values,
        }
        if not values or dispatch["manifest_hash"] != canonical(content):
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        return {
            "dispatch": dispatch,
            "reservation": reservation,
            "manifest": content,
            "manifest_hash": dispatch["manifest_hash"],
        }

    def prepare(self, actor, body, key):
        def write(conn):
            lock_scope(conn, "reservation", body.org_id, body.order_id)
            reservation = self.runtime.one(
                conn,
                self.tables["reservations"],
                body.org_id,
                sales_order_ref=body.order_id,
                warehouse_id=body.warehouse_id,
                lock=True,
            )
            if (
                reservation["version"] != body.expected_reservation_version
                or reservation["status"] != "HELD"
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            table = self.tables["dispatches"]
            existing = (
                conn.execute(
                    sa.select(table).where(
                        table.c.org_id == body.org_id, table.c.reservation_id == reservation["id"]
                    )
                )
                .mappings()
                .all()
            )
            if existing:
                if len(existing) != 1 or existing[0]["status"] != "PREPARED":
                    raise CoreError(ErrorCode.INVALID_STATE)
                return self.manifest(conn, body.org_id, body.warehouse_id, existing[0]["id"])
            if reservation["expires_at"] is not None and reservation["expires_at"] <= datetime.now(
                UTC
            ):
                raise CoreError(ErrorCode.INVALID_STATE)
            lines = (
                conn.execute(
                    sa.select(self.tables["reservation_lines"])
                    .where(
                        self.tables["reservation_lines"].c.org_id == body.org_id,
                        self.tables["reservation_lines"].c.reservation_id == reservation["id"],
                    )
                    .order_by(self.tables["reservation_lines"].c.sales_order_line_ref)
                    .with_for_update()
                )
                .mappings()
                .all()
            )
            if not lines or any(
                line["released_quantity"] or line["dispatched_quantity"] for line in lines
            ):
                raise CoreError(ErrorCode.INVALID_STATE)
            identifier, now = uuid4(), datetime.now(UTC)
            dispatch_lines = [
                {
                    "id": uuid4(),
                    "org_id": body.org_id,
                    "dispatch_id": identifier,
                    "reservation_id": reservation["id"],
                    "reservation_line_id": line["id"],
                    "line_no": i,
                    "quantity": line["allocated_quantity"],
                    "created_at": now,
                }
                for i, line in enumerate(lines, 1)
            ]
            values = []
            for line, dispatch_line in zip(lines, dispatch_lines, strict=True):
                product = self.runtime.one(
                    conn, self.tables["products"], body.org_id, id=line["product_id"]
                )
                values.append(
                    {
                        "dispatch_line_id": str(dispatch_line["id"]),
                        "reservation_line_id": str(line["id"]),
                        "sales_order_line_ref": str(line["sales_order_line_ref"]),
                        "product_ref": str(product["id"]),
                        "sku": product["sku"],
                        "uom": product["uom"],
                        "quantity": format(line["allocated_quantity"], ".3f"),
                    }
                )
            content = {
                "org_id": str(body.org_id),
                "warehouse_ref": str(body.warehouse_id),
                "dispatch_id": str(identifier),
                "reservation_id": str(reservation["id"]),
                "sales_order_ref": str(body.order_id),
                "source_order_revision": reservation["source_order_revision"],
                "manifest_version": 1,
                "lines": values,
            }
            conn.execute(
                table.insert().values(
                    id=identifier,
                    org_id=body.org_id,
                    reservation_id=reservation["id"],
                    dispatch_number="PX-" + identifier.hex[:20],
                    status="PREPARED",
                    manifest_version=1,
                    manifest_hash=canonical(content),
                    prepared_at=now,
                    prepared_by_subject=actor.subject,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(self.tables["dispatch_lines"].insert(), dispatch_lines)
            emit(
                conn,
                self.tables["outbox_events"],
                org_id=body.org_id,
                kind="warehouse.dispatch_prepared",
                aggregate="dispatch",
                identifier=identifier,
                version=1,
                correlation=body.order_id,
                business_key=str(body.order_id),
                payload={
                    "order_id": str(body.order_id),
                    "warehouse_id": str(body.warehouse_id),
                    "dispatch_id": str(identifier),
                    "manifest_hash": canonical(content),
                },
                now=now,
            )
            return self.manifest(conn, body.org_id, body.warehouse_id, identifier)

        return self.command(actor, body, "operations.dispatch.prepare", key, write)

    def handoff(self, actor, body, key, shipment):
        if actor.account_type != "human":
            raise CoreError(ErrorCode.FORBIDDEN)
        if (
            (
                str(shipment.get("org_id")),
                str(shipment.get("warehouse_ref")),
                str(shipment.get("id")),
                str(shipment.get("warehouse_dispatch_ref")),
                shipment.get("manifest_hash"),
            )
            != (
                str(body.org_id),
                str(body.warehouse_id),
                str(body.shipment_id),
                str(body.dispatch_id),
                body.expected_manifest_hash,
            )
            or shipment.get("status") not in {"ACCEPTED", "HANDED_OVER"}
            or not shipment.get("acceptance_ref")
        ):
            raise CoreError(ErrorCode.VERSION_CONFLICT)

        def write(conn):
            dispatch = self.runtime.one(
                conn, self.tables["dispatches"], body.org_id, id=body.dispatch_id, lock=True
            )
            header = self.runtime.one(
                conn,
                self.tables["reservations"],
                body.org_id,
                id=dispatch["reservation_id"],
                warehouse_id=body.warehouse_id,
                lock=True,
            )
            proof = self.manifest(conn, body.org_id, body.warehouse_id, body.dispatch_id)
            if dispatch["manifest_hash"] != body.expected_manifest_hash or str(
                header["sales_order_ref"]
            ) != str(shipment["sales_order_ref"]):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            if dispatch["status"] == "HANDED_OVER":
                event = self.runtime.one(
                    conn, self.tables["outbox_events"], body.org_id, id=dispatch["handoff_event_id"]
                )
                if (
                    str(dispatch["logistics_shipment_ref"]) != str(body.shipment_id)
                    or dispatch["handed_over_at"] != body.handed_over_at
                    or dispatch["carrier_receiver_name"] != body.carrier_receiver_name
                    or event["payload"].get("carrier_receipt_number") != body.carrier_receipt_number
                ):
                    raise CoreError(ErrorCode.VERSION_CONFLICT)
                return proof
            if (
                dispatch["status"] != "PREPARED"
                or dispatch["version"] != body.expected_dispatch_version
                or header["status"] not in {"HELD", "PARTIALLY_DISPATCHED"}
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            now = datetime.now(UTC)
            accepted_at = datetime.fromisoformat(
                str(shipment["accepted_at"]).replace("Z", "+00:00")
            )
            if body.handed_over_at > now or body.handed_over_at < max(
                dispatch["prepared_at"], accepted_at
            ):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            if header["expires_at"] is not None and header["expires_at"] <= body.handed_over_at:
                raise CoreError(ErrorCode.INVALID_STATE)
            lines_table = self.tables["reservation_lines"]
            lines = {
                row["id"]: row
                for row in conn.execute(
                    sa.select(lines_table)
                    .where(
                        lines_table.c.org_id == body.org_id,
                        lines_table.c.reservation_id == header["id"],
                    )
                    .with_for_update()
                ).mappings()
            }
            needs = Counter()
            for line in proof["manifest"]["lines"]:
                source = lines[UUID(line["reservation_line_id"])]
                quantity = Decimal(line["quantity"])
                if (
                    quantity
                    > source["allocated_quantity"]
                    - source["released_quantity"]
                    - source["dispatched_quantity"]
                ):
                    raise CoreError(ErrorCode.VERSION_CONFLICT)
                needs[source["product_id"]] += quantity
            balances = self.tables["inventory_balances"]
            stocks = {
                row["product_id"]: row
                for row in conn.execute(
                    sa.select(balances)
                    .where(
                        balances.c.org_id == body.org_id,
                        balances.c.warehouse_id == body.warehouse_id,
                        balances.c.product_id.in_(needs),
                    )
                    .order_by(balances.c.product_id)
                    .with_for_update()
                ).mappings()
            }
            if set(stocks) != set(needs) or any(
                stocks[pid]["on_hand_quantity"] < quantity
                or stocks[pid]["reserved_quantity"] < quantity
                for pid, quantity in needs.items()
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            for pid, quantity in needs.items():
                conn.execute(
                    balances.update()
                    .where(balances.c.id == stocks[pid]["id"], balances.c.org_id == body.org_id)
                    .values(
                        on_hand_quantity=balances.c.on_hand_quantity - quantity,
                        reserved_quantity=balances.c.reserved_quantity - quantity,
                        version=balances.c.version + 1,
                        updated_at=now,
                    )
                )
            for line in proof["manifest"]["lines"]:
                source = lines[UUID(line["reservation_line_id"])]
                quantity = Decimal(line["quantity"])
                conn.execute(
                    lines_table.update()
                    .where(lines_table.c.org_id == body.org_id, lines_table.c.id == source["id"])
                    .values(
                        dispatched_quantity=lines_table.c.dispatched_quantity + quantity,
                        updated_at=now,
                    )
                )
                conn.execute(
                    self.tables["stock_movements"]
                    .insert()
                    .values(
                        id=uuid4(),
                        org_id=body.org_id,
                        warehouse_id=body.warehouse_id,
                        product_id=source["product_id"],
                        movement_type="DISPATCH",
                        quantity_delta=-quantity,
                        dispatch_line_id=UUID(line["dispatch_line_id"]),
                        actor_subject=actor.subject,
                        correlation_id=header["sales_order_ref"],
                        occurred_at=body.handed_over_at,
                        recorded_at=now,
                    )
                )
            event_id = emit(
                conn,
                self.tables["outbox_events"],
                org_id=body.org_id,
                kind="warehouse.dispatch_handed_over",
                aggregate="dispatch",
                identifier=body.dispatch_id,
                version=dispatch["version"] + 1,
                correlation=header["sales_order_ref"],
                business_key=str(header["sales_order_ref"]),
                payload={
                    "order_id": str(header["sales_order_ref"]),
                    "warehouse_id": str(body.warehouse_id),
                    "dispatch_id": str(body.dispatch_id),
                    "shipment_id": str(body.shipment_id),
                    "manifest_hash": dispatch["manifest_hash"],
                    "handed_over_at": body.handed_over_at,
                    "carrier_receiver_name": body.carrier_receiver_name,
                    "carrier_receipt_number": body.carrier_receipt_number,
                },
                now=now,
            )
            table = self.tables["dispatches"]
            conn.execute(
                table.update()
                .where(table.c.id == body.dispatch_id, table.c.org_id == body.org_id)
                .values(
                    status="HANDED_OVER",
                    logistics_shipment_ref=body.shipment_id,
                    logistics_acceptance_ref=UUID(str(shipment["acceptance_ref"])),
                    carrier_code_snapshot=shipment["carrier_code_snapshot"],
                    carrier_name_snapshot=shipment["carrier_name_snapshot"],
                    carrier_receiver_name=body.carrier_receiver_name,
                    handoff_event_id=event_id,
                    handed_over_at=body.handed_over_at,
                    handed_over_by_subject=actor.subject,
                    version=table.c.version + 1,
                    updated_at=now,
                )
            )
            all_lines = (
                conn.execute(
                    sa.select(lines_table).where(
                        lines_table.c.org_id == body.org_id,
                        lines_table.c.reservation_id == header["id"],
                    )
                )
                .mappings()
                .all()
            )
            done = all(
                row["dispatched_quantity"] == row["requested_quantity"]
                and row["released_quantity"] == 0
                for row in all_lines
            )
            table = self.tables["reservations"]
            conn.execute(
                table.update()
                .where(table.c.org_id == body.org_id, table.c.id == header["id"])
                .values(
                    status="DISPATCHED" if done else "PARTIALLY_DISPATCHED",
                    version=table.c.version + 1,
                    updated_at=now,
                )
            )
            return self.manifest(conn, body.org_id, body.warehouse_id, body.dispatch_id)

        return self.command(actor, body, "operations.dispatch.handoff", key, write)

    def handoff_proof(self, conn, body, order):
        self.order_source(body, order)
        snapshot = self.reservation_snapshot(conn, body.org_id, body.warehouse_id, body.order_id)
        if snapshot["source_order_revision"] != order["input_revision"]:
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        original = {str(line["id"]): line for line in order["lines"]}
        links = {str(line["id"]): line for line in snapshot["lines"]}
        done, events = Counter(), []
        for dispatch in snapshot["dispatches"]:
            if dispatch["status"] != "HANDED_OVER":
                continue
            manifest = self.manifest(conn, body.org_id, body.warehouse_id, dispatch["id"])
            event = self.runtime.one(
                conn, self.tables["outbox_events"], body.org_id, id=dispatch["handoff_event_id"]
            )
            if (
                event["event_type"] != "warehouse.dispatch_handed_over"
                or event["aggregate_id"] != dispatch["id"]
                or event["payload_hash"] != canonical(event["payload"])
                or not event["payload"].get("carrier_receipt_number")
                or not dispatch["carrier_receiver_name"]
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            for line in manifest["manifest"]["lines"]:
                if line["sales_order_line_ref"] not in original:
                    raise CoreError(ErrorCode.VERSION_CONFLICT)
                source = original[line["sales_order_line_ref"]]
                if (line["product_ref"], line["sku"], line["uom"]) != (
                    str(source["product_ref"]),
                    source["sku"],
                    source["uom"],
                ):
                    raise CoreError(ErrorCode.VERSION_CONFLICT)
                done[line["sales_order_line_ref"]] += Decimal(line["quantity"])
            events.append(
                {
                    "source_event_id": str(event["id"]),
                    "dispatch_id": str(dispatch["id"]),
                    "source_version": dispatch["version"],
                    "manifest_hash": dispatch["manifest_hash"],
                    "handed_over_at": dispatch["handed_over_at"].isoformat(),
                }
            )
        if any(
            done.get(identifier, 0) > Decimal(str(line["ordered_quantity"]))
            for identifier, line in original.items()
        ):
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        for line in links.values():
            if (
                str(line["sales_order_line_ref"]) not in original
                or done.get(str(line["sales_order_line_ref"]), 0) != line["dispatched_quantity"]
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
        complete = bool(events) and all(
            done.get(identifier, 0) == Decimal(str(line["ordered_quantity"]))
            for identifier, line in original.items()
        )
        latest = max(
            (datetime.fromisoformat(event["handed_over_at"]) for event in events), default=None
        )
        return {
            "org_id": str(body.org_id),
            "warehouse_id": str(body.warehouse_id),
            "order_id": str(body.order_id),
            "source_order_revision": snapshot["source_order_revision"],
            "complete": complete,
            "completed_at": latest.isoformat() if complete else None,
            "evidence": events,
            "lines": [
                {
                    "order_line_id": identifier,
                    "sku": line["sku"],
                    "uom": line["uom"],
                    "ordered_quantity": str(line["ordered_quantity"]),
                    "handed_over_quantity": str(done.get(identifier, 0)),
                }
                for identifier, line in original.items()
            ],
        }

    def plan(self, conn, body):
        row = self.runtime.one(
            conn,
            self.tables["inbound_plans"],
            body.org_id,
            id=body.inbound_plan_id,
            warehouse_id=body.warehouse_id,
            lock=True,
        )
        if row["updated_at"] != body.expected_updated_at or row["status"] not in {
            "APPROVED",
            "PARTIALLY_RECEIVED",
        }:
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        return row

    def eta(self, actor, body, key):
        def write(conn):
            row, now = self.plan(conn, body), datetime.now(UTC)
            if body.expected_at < now:
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            table = self.tables["inbound_plans"]
            result = dict(
                conn.execute(
                    table.update()
                    .where(table.c.id == row["id"], table.c.org_id == body.org_id)
                    .values(expected_at=body.expected_at, updated_at=now)
                    .returning(table)
                )
                .mappings()
                .one()
            )
            emit(
                conn,
                self.tables["outbox_events"],
                org_id=body.org_id,
                kind="warehouse.inbound_eta_changed",
                aggregate="inbound_plan",
                identifier=row["id"],
                version=int(now.timestamp() * 1000000),
                correlation=row["id"],
                payload={
                    "warehouse_id": str(body.warehouse_id),
                    "product_id": str(row["product_id"]),
                    "previous_expected_at": row["expected_at"],
                    "expected_at": body.expected_at,
                },
                now=now,
            )
            return result

        return self.command(actor, body, "operations.inbound.update_eta", key, write)

    def receive(self, actor, body, key):
        def write(conn):
            row, now = self.plan(conn, body), datetime.now(UTC)
            if body.received_at > now or body.received_at < row["created_at"]:
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            receipts, lines = self.tables["goods_receipts"], self.tables["goods_receipt_lines"]
            accepted = conn.scalar(
                sa.select(sa.func.coalesce(sa.func.sum(lines.c.accepted_quantity), 0))
                .select_from(
                    lines.join(
                        receipts,
                        sa.and_(
                            lines.c.receipt_id == receipts.c.id, lines.c.org_id == receipts.c.org_id
                        ),
                    )
                )
                .where(
                    lines.c.org_id == body.org_id,
                    lines.c.inbound_plan_id == row["id"],
                    receipts.c.status == "POSTED",
                )
            )
            if accepted + body.accepted_quantity > row["planned_quantity"]:
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            self.runtime.one(
                conn, self.tables["warehouses"], body.org_id, id=body.warehouse_id, status="ACTIVE"
            )
            self.runtime.one(
                conn, self.tables["products"], body.org_id, id=row["product_id"], status="ACTIVE"
            )
            lock_scope(conn, "balance", body.org_id, body.warehouse_id, row["product_id"])
            balances = self.tables["inventory_balances"]
            stock = (
                conn.execute(
                    sa.select(balances)
                    .where(
                        balances.c.org_id == body.org_id,
                        balances.c.warehouse_id == body.warehouse_id,
                        balances.c.product_id == row["product_id"],
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if stock is None:
                stock = (
                    conn.execute(
                        balances.insert()
                        .values(
                            id=uuid4(),
                            org_id=body.org_id,
                            warehouse_id=body.warehouse_id,
                            product_id=row["product_id"],
                            on_hand_quantity=0,
                            reserved_quantity=0,
                            version=1,
                            updated_at=now,
                        )
                        .returning(balances)
                    )
                    .mappings()
                    .one()
                )
            receipt_id, line_id = uuid4(), uuid4()
            conn.execute(
                receipts.insert().values(
                    id=receipt_id,
                    org_id=body.org_id,
                    warehouse_id=body.warehouse_id,
                    receipt_number=body.receipt_number,
                    source_type="REPLENISHMENT",
                    source_document_ref=str(row["id"]),
                    status="POSTED",
                    received_at=body.received_at,
                    posted_at=now,
                    received_by_subject=actor.subject,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                lines.insert().values(
                    id=line_id,
                    org_id=body.org_id,
                    inbound_plan_id=row["id"],
                    receipt_id=receipt_id,
                    line_no=1,
                    product_id=row["product_id"],
                    received_quantity=body.received_quantity,
                    accepted_quantity=body.accepted_quantity,
                    rejection_reason=body.rejection_reason,
                    created_at=now,
                )
            )
            if body.accepted_quantity > 0:
                conn.execute(
                    balances.update()
                    .where(balances.c.org_id == body.org_id, balances.c.id == stock["id"])
                    .values(
                        on_hand_quantity=balances.c.on_hand_quantity + body.accepted_quantity,
                        version=balances.c.version + 1,
                        updated_at=now,
                    )
                )
                conn.execute(
                    self.tables["stock_movements"]
                    .insert()
                    .values(
                        id=uuid4(),
                        org_id=body.org_id,
                        warehouse_id=body.warehouse_id,
                        product_id=row["product_id"],
                        movement_type="RECEIPT",
                        quantity_delta=body.accepted_quantity,
                        goods_receipt_line_id=line_id,
                        actor_subject=actor.subject,
                        correlation_id=receipt_id,
                        occurred_at=body.received_at,
                        recorded_at=now,
                    )
                )
            total = accepted + body.accepted_quantity
            conn.execute(
                self.tables["inbound_plans"]
                .update()
                .where(
                    self.tables["inbound_plans"].c.id == row["id"],
                    self.tables["inbound_plans"].c.org_id == body.org_id,
                )
                .values(
                    status="RECEIVED"
                    if total == row["planned_quantity"]
                    else "PARTIALLY_RECEIVED"
                    if total > 0
                    else row["status"],
                    updated_at=now,
                )
            )
            emit(
                conn,
                self.tables["outbox_events"],
                org_id=body.org_id,
                kind="warehouse.receipt_posted",
                aggregate="goods_receipt",
                identifier=receipt_id,
                version=1,
                correlation=receipt_id,
                payload={
                    "warehouse_id": str(body.warehouse_id),
                    "inbound_plan_id": str(row["id"]),
                    "product_id": str(row["product_id"]),
                    "accepted_quantity": str(body.accepted_quantity),
                },
                now=now,
            )
            return jsonable_encoder(
                {
                    "receipt_id": receipt_id,
                    "receipt_number": body.receipt_number,
                    "accepted_quantity": str(body.accepted_quantity),
                    "inbound_plan_id": row["id"],
                    "posted_at": now,
                },
                custom_encoder={Decimal: str},
            )

        return self.command(actor, body, "operations.receipts.post", key, write)

    def handoff_event(self, conn, org_id, warehouse_id, dispatch_id):
        value = self.manifest(conn, org_id, warehouse_id, dispatch_id)
        dispatch = value["dispatch"]
        if dispatch["status"] != "HANDED_OVER" or dispatch["handed_over_at"] > datetime.now(UTC):
            raise CoreError(ErrorCode.INVALID_STATE)
        event = self.runtime.one(
            conn, self.tables["outbox_events"], org_id, id=dispatch["handoff_event_id"]
        )
        if (
            event["aggregate_id"] != dispatch_id
            or event["event_type"] != "warehouse.dispatch_handed_over"
            or canonical(event["payload"]) != event["payload_hash"]
        ):
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        return {
            "producer_app": "warehouse",
            **{
                key: event[key]
                for key in (
                    "id",
                    "org_id",
                    "event_type",
                    "event_schema_version",
                    "aggregate_type",
                    "aggregate_id",
                    "aggregate_version",
                    "business_key",
                    "correlation_id",
                    "payload",
                    "payload_hash",
                    "occurred_at",
                )
            },
        }


def install(app, runtime, tables):
    operations = WarehouseOperations(runtime, tables)
    actor = Depends(authenticated_identity)

    @app.post("/api/v1/warehouse/operations/commands/read")
    def receipt_status(body: CommandReceiptQuery, identity=actor):
        return command_receipt(runtime, identity, body)

    @app.post("/api/v1/warehouse/operations/inventory/read")
    def read_inventory(body: WarehouseScope, identity=actor):
        return inventory(body.org_id, body.warehouse_id, identity)

    @app.post("/api/v1/warehouse/operations/inbound/read")
    def read_inbound(body: ReadInbound, identity=actor):
        return plan(body.inbound_plan_id, body.org_id, body.warehouse_id, identity)

    @app.post("/api/v1/warehouse/operations/reservations/read")
    def read_reservation(body: ReadOrder, identity=actor):
        return reservation(body.order_id, body.org_id, body.warehouse_id, identity)

    @app.post("/api/v1/warehouse/operations/dispatches/read")
    def read_dispatch(body: ReadDispatch, identity=actor):
        return manifest(body.dispatch_id, body.org_id, body.warehouse_id, identity)

    @app.post("/api/v1/warehouse/operations/handoff-proof/read")
    def read_proof(body: ReadOrder, identity=actor, authorization: str = Header()):
        return handoff_proof(body.order_id, body.org_id, body.warehouse_id, identity, authorization)

    @app.get("/api/v1/warehouse/operations/dispatches/{dispatch_id}/handoff-event")
    def handoff_event(dispatch_id: UUID, org_id: UUID, warehouse_id: UUID, identity=actor):
        allow_scope(runtime, identity, org_id, warehouse_id, "operations.handoff_proof.read")
        with runtime.engine.connect() as conn:
            return operations.handoff_event(conn, org_id, warehouse_id, dispatch_id)

    @app.get("/api/v1/warehouse/operations/inventory")
    def inventory(org_id: UUID, warehouse_id: UUID, identity=actor):
        allow_scope(runtime, identity, org_id, warehouse_id, "operations.inventory.read")
        with runtime.engine.connect() as conn:
            table = tables["inventory_balances"]
            return jsonable_encoder(
                {
                    "items": [
                        dict(row)
                        for row in conn.execute(
                            sa.select(table).where(
                                table.c.org_id == org_id, table.c.warehouse_id == warehouse_id
                            )
                        ).mappings()
                    ]
                },
                custom_encoder={Decimal: str},
            )

    @app.get("/api/v1/warehouse/operations/reservations/by-order/{order_id}")
    def reservation(order_id: UUID, org_id: UUID, warehouse_id: UUID, identity=actor):
        allow_scope(runtime, identity, org_id, warehouse_id, "operations.reservations.read")
        with runtime.engine.connect() as conn:
            return jsonable_encoder(
                operations.reservation_snapshot(conn, org_id, warehouse_id, order_id),
                custom_encoder={Decimal: str},
            )

    @app.get("/api/v1/warehouse/operations/dispatches/{dispatch_id}")
    def manifest(dispatch_id: UUID, org_id: UUID, warehouse_id: UUID, identity=actor):
        allow_scope(runtime, identity, org_id, warehouse_id, "operations.dispatch.read")
        with runtime.engine.connect() as conn:
            return jsonable_encoder(
                operations.manifest(conn, org_id, warehouse_id, dispatch_id),
                custom_encoder={Decimal: str},
            )

    @app.get("/api/v1/warehouse/operations/orders/{order_id}/handoff-proof")
    def handoff_proof(
        order_id: UUID,
        org_id: UUID,
        warehouse_id: UUID,
        identity=actor,
        authorization: str = Header(),
    ):
        allow_scope(runtime, identity, org_id, warehouse_id, "operations.handoff_proof.read")
        order = runtime.remote(
            "sales",
            "GET",
            f"/api/v1/sales/operations/orders/{order_id}?org_id={org_id}&warehouse_id={warehouse_id}",
            authorization.partition(" ")[2],
        )
        from types import SimpleNamespace

        with runtime.engine.connect() as conn:
            return operations.handoff_proof(
                conn,
                SimpleNamespace(org_id=org_id, warehouse_id=warehouse_id, order_id=order_id),
                order,
            )

    @app.post("/api/v1/warehouse/operations/dispatches/prepare")
    def prepare(body: PrepareCommand, identity=actor, idempotency_key: str = Header(default="")):
        return operations.prepare(identity, body, idempotency_key)

    @app.post("/api/v1/warehouse/operations/dispatches/handoff")
    def handoff(
        body: HandoffCommand,
        identity=actor,
        authorization: str = Header(),
        idempotency_key: str = Header(default=""),
    ):
        operations.scoped(identity, body, "operations.dispatch.handoff")
        if identity.account_type != "human":
            raise CoreError(ErrorCode.FORBIDDEN)
        shipment = runtime.remote(
            "logistics",
            "GET",
            f"/api/v1/logistics/operations/shipments/{body.shipment_id}?org_id={body.org_id}&warehouse_id={body.warehouse_id}",
            authorization.partition(" ")[2],
        )
        return operations.handoff(identity, body, idempotency_key, shipment)

    @app.post("/api/v1/warehouse/operations/reservations", status_code=201)
    def reserve(
        body: ReserveCommand,
        identity=actor,
        authorization: str = Header(),
        idempotency_key: str = Header(default=""),
    ):
        operations.scoped(identity, body, "operations.stock.reserve")
        order = runtime.remote(
            "sales",
            "GET",
            f"/api/v1/sales/operations/orders/{body.order_id}?org_id={body.org_id}&warehouse_id={body.warehouse_id}",
            authorization.partition(" ")[2],
        )
        return operations.reserve(identity, body, idempotency_key, order)

    @app.get("/api/v1/warehouse/operations/inbound/{plan_id}")
    def plan(plan_id: UUID, org_id: UUID, warehouse_id: UUID, identity=actor):
        allow_scope(runtime, identity, org_id, warehouse_id, "operations.inbound.read")
        return jsonable_encoder(
            runtime.fetch(tables["inbound_plans"], org_id, id=plan_id, warehouse_id=warehouse_id),
            custom_encoder={Decimal: str},
        )

    @app.post("/api/v1/warehouse/operations/preparation")
    def preparation(
        body: PreparationCommand, identity=actor, idempotency_key: str = Header(default="")
    ):
        return operations.preparation(identity, body, idempotency_key)

    @app.post("/api/v1/warehouse/operations/inbound/eta")
    def eta(body: EtaCommand, identity=actor, idempotency_key: str = Header(default="")):
        return operations.eta(identity, body, idempotency_key)

    @app.post("/api/v1/warehouse/operations/receipts", status_code=201)
    def receive(body: ReceiveCommand, identity=actor, idempotency_key: str = Header(default="")):
        return operations.receive(identity, body, idempotency_key)
