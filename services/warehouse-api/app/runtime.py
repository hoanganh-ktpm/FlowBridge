"""Locked all-or-nothing reservation, packing and evidenced physical handoff."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import Depends, Header

from app.db.models import WarehouseBase
from app.platform.business import BusinessRuntime, StrictBody
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import authenticated_identity

tables = {t.name: t for t in WarehouseBase.metadata.sorted_tables}


class OrderRef(StrictBody):
    org_id: UUID
    order_id: UUID


def canonical(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def create_app():
    runtime = BusinessRuntime("warehouse", tables["command_receipts"])
    app = runtime.application()
    actor = Depends(authenticated_identity)

    def reservation(conn, body, lock=False):
        return runtime.one(
            conn, tables["reservations"], body.org_id, sales_order_ref=body.order_id, lock=lock
        )

    def snapshot(conn, body):
        header = reservation(conn, body)
        lines = (
            conn.execute(
                sa.select(tables["reservation_lines"]).where(
                    tables["reservation_lines"].c.reservation_id == header["id"],
                    tables["reservation_lines"].c.org_id == body.org_id,
                )
            )
            .mappings()
            .all()
        )
        dispatch = (
            conn.execute(
                sa.select(tables["dispatches"]).where(
                    tables["dispatches"].c.reservation_id == header["id"],
                    tables["dispatches"].c.org_id == body.org_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return {
            **header,
            "lines": [dict(line) for line in lines],
            "dispatch": dict(dispatch) if dispatch else None,
        }

    @app.get("/api/v1/warehouse/workflow/inventory")
    def inventory(org_id: UUID, warehouse_id: UUID, identity=actor):
        runtime.allow(identity, org_id, "inventory.read")
        balance = tables["inventory_balances"]
        with runtime.engine.connect() as conn:
            return {
                "rows": [
                    dict(row)
                    for row in conn.execute(
                        sa.select(balance).where(
                            balance.c.org_id == org_id,
                            balance.c.warehouse_id == warehouse_id,
                        )
                    ).mappings()
                ]
            }

    @app.get("/api/v1/warehouse/workflow/reservations/{order_id}")
    def get(order_id: UUID, org_id: UUID, identity=actor):
        runtime.allow(identity, org_id, "reservations.read")
        with runtime.engine.connect() as conn:
            return snapshot(conn, OrderRef(org_id=org_id, order_id=order_id))

    @app.post("/api/v1/warehouse/workflow/reservations", status_code=201)
    def reserve(
        body: OrderRef,
        identity=actor,
        authorization: str = Header(),
        idempotency_key: str = Header(default=""),
    ):
        runtime.allow(identity, body.org_id, "stock.reserve")
        order = runtime.remote(
            "sales",
            "GET",
            f"/api/v1/sales/workflow/orders/{body.order_id}?org_id={body.org_id}",
            authorization.partition(" ")[2],
        )
        if (
            order["org_id"] != str(body.org_id)
            or not order["order_number"].startswith("wf_e2e_")
            or order["status"] not in {"CONFIRMED", "RESERVED", "PACKED", "SHIPPED"}
        ):
            raise CoreError(ErrorCode.INVALID_STATE)

        def write(conn):
            conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                {"scope": f"reserve:{body.org_id}:{body.order_id}"},
            )
            existing = (
                conn.execute(
                    sa.select(tables["reservations"]).where(
                        tables["reservations"].c.org_id == body.org_id,
                        tables["reservations"].c.sales_order_ref == body.order_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing:
                return snapshot(conn, body)
            needs = Counter()
            for line in order["lines"]:
                needs[UUID(line["product_ref"])] += Decimal(line["quantity"])
            balance = tables["inventory_balances"]
            stocks = {
                r["product_id"]: r
                for r in conn.execute(
                    sa.select(balance)
                    .where(
                        balance.c.org_id == body.org_id,
                        balance.c.warehouse_id == UUID(order["warehouse_ref"]),
                        balance.c.product_id.in_(needs),
                    )
                    .order_by(balance.c.product_id)
                    .with_for_update()
                ).mappings()
            }
            if len(stocks) != len(needs) or any(
                q <= 0 or stocks[pid]["on_hand_quantity"] - stocks[pid]["reserved_quantity"] < q
                for pid, q in needs.items()
            ):
                raise CoreError(ErrorCode.INVALID_STATE)
            products = {
                r["id"]: r
                for r in conn.execute(
                    sa.select(tables["products"]).where(
                        tables["products"].c.org_id == body.org_id,
                        tables["products"].c.id.in_(needs),
                    )
                ).mappings()
            }
            if any(
                (
                    products[UUID(line["product_ref"])]["sku"],
                    products[UUID(line["product_ref"])]["uom"],
                )
                != (line["sku"], line["uom"])
                for line in order["lines"]
            ):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            identifier, now = uuid4(), datetime.now(UTC)
            conn.execute(
                tables["reservations"]
                .insert()
                .values(
                    id=identifier,
                    org_id=body.org_id,
                    reservation_number="wf_e2e_" + identifier.hex[:16],
                    sales_order_ref=body.order_id,
                    sales_order_number=order["order_number"],
                    source_order_revision=1,
                    warehouse_id=UUID(order["warehouse_ref"]),
                    status="HELD",
                    requested_by_subject=identity.subject,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                tables["reservation_lines"].insert(),
                [
                    {
                        "id": uuid4(),
                        "org_id": body.org_id,
                        "reservation_id": identifier,
                        "sales_order_line_ref": UUID(line["line_id"]),
                        "product_id": UUID(line["product_ref"]),
                        "requested_quantity": Decimal(line["quantity"]),
                        "allocated_quantity": Decimal(line["quantity"]),
                        "released_quantity": Decimal(0),
                        "dispatched_quantity": Decimal(0),
                        "created_at": now,
                        "updated_at": now,
                    }
                    for line in order["lines"]
                ],
            )
            for pid, q in needs.items():
                conn.execute(
                    balance.update()
                    .where(balance.c.id == stocks[pid]["id"])
                    .values(
                        reserved_quantity=balance.c.reserved_quantity + q,
                        version=balance.c.version + 1,
                        updated_at=now,
                    )
                )
            return snapshot(conn, body)

        return runtime.command(identity, body, "stock.reserve", idempotency_key, write)

    @app.post("/api/v1/warehouse/workflow/pack")
    def pack(body: OrderRef, identity=actor, idempotency_key: str = Header(default="")):
        def write(conn):
            header = reservation(conn, body, lock=True)
            current = snapshot(conn, body)
            if current["dispatch"]:
                return current
            if header["status"] != "HELD":
                raise CoreError(ErrorCode.INVALID_STATE)
            identifier, now = uuid4(), datetime.now(UTC)
            manifest = {
                "order_id": str(body.order_id),
                "org_id": str(body.org_id),
                "lines": [
                    {
                        "line_id": str(line["sales_order_line_ref"]),
                        "product_id": str(line["product_id"]),
                        "quantity": str(line["allocated_quantity"]),
                    }
                    for line in current["lines"]
                ],
            }
            conn.execute(
                tables["dispatches"]
                .insert()
                .values(
                    id=identifier,
                    org_id=body.org_id,
                    dispatch_number="wf_e2e_" + identifier.hex[:16],
                    reservation_id=header["id"],
                    status="PREPARED",
                    manifest_version=1,
                    manifest_hash=canonical(manifest),
                    prepared_at=now,
                    prepared_by_subject=identity.subject,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                tables["dispatch_lines"].insert(),
                [
                    {
                        "id": uuid4(),
                        "org_id": body.org_id,
                        "dispatch_id": identifier,
                        "reservation_id": header["id"],
                        "reservation_line_id": line["id"],
                        "line_no": i,
                        "quantity": line["allocated_quantity"],
                        "created_at": now,
                    }
                    for i, line in enumerate(current["lines"], 1)
                ],
            )
            return snapshot(conn, body)

        return runtime.command(identity, body, "stock.pack", idempotency_key, write)

    @app.post("/api/v1/warehouse/workflow/handoff")
    def handoff(
        body: OrderRef,
        identity=actor,
        authorization: str = Header(),
        idempotency_key: str = Header(default=""),
    ):
        runtime.allow(identity, body.org_id, "stock.handoff")
        shipment = runtime.remote(
            "logistics",
            "GET",
            f"/api/v1/logistics/workflow/shipments/by-order/{body.order_id}?org_id={body.org_id}",
            authorization.partition(" ")[2],
        )

        def write(conn):
            header = reservation(conn, body, lock=True)
            current = snapshot(conn, body)
            dispatch = current["dispatch"]
            if not dispatch or str(dispatch["id"]) != shipment["warehouse_dispatch_ref"]:
                raise CoreError(ErrorCode.INVALID_STATE)
            if dispatch["status"] == "HANDED_OVER":
                return current
            if dispatch["status"] != "PREPARED" or shipment["status"] != "CREATED":
                raise CoreError(ErrorCode.INVALID_STATE)
            balance = tables["inventory_balances"]
            stocks = {
                r["product_id"]: r
                for r in conn.execute(
                    sa.select(balance)
                    .where(
                        balance.c.org_id == body.org_id,
                        balance.c.warehouse_id == header["warehouse_id"],
                        balance.c.product_id.in_([line["product_id"] for line in current["lines"]]),
                    )
                    .order_by(balance.c.product_id)
                    .with_for_update()
                ).mappings()
            }
            now, evidence = datetime.now(UTC), uuid4()
            dispatch_lines = (
                conn.execute(
                    sa.select(tables["dispatch_lines"]).where(
                        tables["dispatch_lines"].c.dispatch_id == dispatch["id"],
                    )
                )
                .mappings()
                .all()
            )
            reserved_lines = {line["id"]: line for line in current["lines"]}
            for line in dispatch_lines:
                reserved = reserved_lines[line["reservation_line_id"]]
                stock = stocks[reserved["product_id"]]
                conn.execute(
                    balance.update()
                    .where(balance.c.id == stock["id"])
                    .values(
                        on_hand_quantity=balance.c.on_hand_quantity - line["quantity"],
                        reserved_quantity=balance.c.reserved_quantity - line["quantity"],
                        version=balance.c.version + 1,
                        updated_at=now,
                    )
                )
                conn.execute(
                    tables["reservation_lines"]
                    .update()
                    .where(tables["reservation_lines"].c.id == reserved["id"])
                    .values(dispatched_quantity=line["quantity"], updated_at=now)
                )
                conn.execute(
                    tables["stock_movements"]
                    .insert()
                    .values(
                        id=uuid4(),
                        org_id=body.org_id,
                        warehouse_id=header["warehouse_id"],
                        product_id=reserved["product_id"],
                        movement_type="DISPATCH",
                        quantity_delta=-line["quantity"],
                        dispatch_line_id=line["id"],
                        actor_subject=identity.subject,
                        correlation_id=body.order_id,
                        occurred_at=now,
                        recorded_at=now,
                    )
                )
            payload = {
                "order_id": str(body.order_id),
                "dispatch_id": str(dispatch["id"]),
                "handed_over_at": now.isoformat(),
            }
            conn.execute(
                tables["outbox_events"]
                .insert()
                .values(
                    id=evidence,
                    org_id=body.org_id,
                    event_type="warehouse.dispatch_handed_over",
                    aggregate_type="dispatch",
                    aggregate_id=dispatch["id"],
                    aggregate_version=2,
                    business_key=header["sales_order_number"],
                    correlation_id=body.order_id,
                    payload=payload,
                    payload_hash=canonical(payload),
                    occurred_at=now,
                )
            )
            conn.execute(
                tables["dispatches"]
                .update()
                .where(tables["dispatches"].c.id == dispatch["id"])
                .values(
                    status="HANDED_OVER",
                    logistics_shipment_ref=UUID(shipment["id"]),
                    logistics_acceptance_ref=UUID(shipment["id"]),
                    carrier_code_snapshot="WF-E2E",
                    carrier_name_snapshot="Workflow demo carrier",
                    carrier_receiver_name="E2E receiver",
                    handoff_event_id=evidence,
                    handed_over_at=now,
                    handed_over_by_subject=identity.subject,
                    version=2,
                    updated_at=now,
                )
            )
            conn.execute(
                tables["reservations"]
                .update()
                .where(tables["reservations"].c.id == header["id"])
                .values(status="DISPATCHED", version=2, updated_at=now)
            )
            return snapshot(conn, body)

        return runtime.command(identity, body, "stock.handoff", idempotency_key, write)

    from .operations import install

    install(app, runtime, tables)
    return app
