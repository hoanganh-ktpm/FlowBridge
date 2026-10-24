"""Minimal isolated delivery workflow, preserving historical Sales semantics."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import Depends, Header
from pydantic import Field

from app.db.models import SalesBase
from app.platform.business import BusinessRuntime, StrictBody
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import authenticated_identity

orders = SalesBase.metadata.tables["public.wf_e2e_orders"]
receipts = SalesBase.metadata.tables["public.command_receipts"]


class Line(StrictBody):
    product_ref: UUID
    sku: str = Field(min_length=1, max_length=80)
    uom: str = Field(default="EA", pattern="^EA$")
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)


class CreateOrder(StrictBody):
    org_id: UUID
    order_number: str = Field(pattern=r"^wf_e2e_[a-zA-Z0-9_-]{1,60}$")
    warehouse_ref: UUID
    lines: list[Line] = Field(min_length=1, max_length=20)


class OrderRef(StrictBody):
    org_id: UUID
    order_id: UUID


class ChangeStatus(OrderRef):
    status: str = Field(pattern="^(WAITING_STOCK|RESERVED|PACKED|SHIPPED|DELIVERED|FAILED)$")
    workflow_instance_ref: str | None = Field(default=None, max_length=128)
    source_event_ref: UUID | None = None


def create_app():
    runtime = BusinessRuntime("sales", receipts)
    app = runtime.application()
    actor = Depends(authenticated_identity)

    @app.post("/api/v1/sales/workflow/orders", status_code=201)
    def create(body: CreateOrder, identity=actor, idempotency_key: str = Header(default="")):
        def write(conn):
            existing = (
                conn.execute(
                    sa.select(orders).where(
                        orders.c.org_id == body.org_id,
                        orders.c.order_number == body.order_number,
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            values = body.model_dump(mode="json")
            values.update(
                id=uuid4(),
                org_id=body.org_id,
                warehouse_ref=body.warehouse_ref,
                owner_subject=identity.subject,
                status="CONFIRMED",
                version=1,
            )
            values["lines"] = [{**line, "line_id": str(uuid4())} for line in values["lines"]]
            return dict(
                conn.execute(orders.insert().values(**values).returning(orders)).mappings().one()
            )

        return runtime.command(identity, body, "orders.create", idempotency_key, write)

    @app.get("/api/v1/sales/workflow/orders/{order_id}")
    def get(order_id: UUID, org_id: UUID, identity=actor):
        runtime.allow(identity, org_id, "orders.read")
        return runtime.fetch(orders, org_id, id=order_id)

    @app.post("/api/v1/sales/workflow/orders/status")
    def status(
        body: ChangeStatus,
        identity=actor,
        authorization: str = Header(),
        idempotency_key: str = Header(default=""),
    ):
        runtime.allow(identity, body.org_id, "orders.status")
        token = authorization.partition(" ")[2]
        # DELIVERED needs an independently authorized Logistics fact, not a client flag.
        shipment = None
        if body.status in {"SHIPPED", "DELIVERED"}:
            shipment = runtime.remote(
                "logistics",
                "GET",
                f"/api/v1/logistics/workflow/shipments/by-order/{body.order_id}?org_id={body.org_id}",
                token,
            )
            if (body.status == "DELIVERED" and shipment["status"] != "DELIVERED") or (
                body.source_event_ref and shipment["event_id"] != str(body.source_event_ref)
            ):
                raise CoreError(ErrorCode.INVALID_STATE)
        if body.status in {"RESERVED", "PACKED"}:
            reservation = runtime.remote(
                "warehouse",
                "GET",
                f"/api/v1/warehouse/workflow/reservations/{body.order_id}?org_id={body.org_id}",
                token,
            )
            if body.status == "PACKED" and not reservation.get("dispatch"):
                raise CoreError(ErrorCode.INVALID_STATE)
        if body.workflow_instance_ref:
            instance = runtime.remote(
                "workflow",
                "GET",
                f"/api/v1/workflows/instances/{body.workflow_instance_ref}",
                token,
            )
            if instance["business_key"] != str(body.order_id):
                raise CoreError(ErrorCode.INVALID_STATE)

        def write(conn):
            current = runtime.one(conn, orders, body.org_id, id=body.order_id, lock=True)
            if current["status"] == body.status and (
                not body.workflow_instance_ref
                or current["workflow_instance_ref"] == body.workflow_instance_ref
            ):
                return current
            transitions = {
                "CONFIRMED": {"WAITING_STOCK", "RESERVED", "FAILED"},
                "WAITING_STOCK": {"RESERVED", "FAILED"},
                "RESERVED": {"PACKED", "SHIPPED"},
                "PACKED": {"SHIPPED"},
                "SHIPPED": {"DELIVERED"},
            }
            if current["status"] != body.status and body.status not in transitions.get(
                current["status"], set()
            ):
                raise CoreError(ErrorCode.INVALID_STATE)
            values = {
                "status": body.status,
                "version": current["version"] + 1,
                "updated_at": datetime.now(UTC),
            }
            if body.workflow_instance_ref:
                values["workflow_instance_ref"] = body.workflow_instance_ref
            if shipment and body.status == "DELIVERED":
                values["last_source_event_ref"] = UUID(shipment["event_id"])
            return dict(
                conn.execute(
                    orders.update()
                    .where(orders.c.id == body.order_id)
                    .values(**values)
                    .returning(orders)
                )
                .mappings()
                .one()
            )

        return runtime.command(identity, body, "orders.status", idempotency_key, write)

    return app
