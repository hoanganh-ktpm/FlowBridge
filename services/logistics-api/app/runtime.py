"""Delivery slice with trusted Warehouse packing/handoff evidence over HTTP."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import Depends, Header
from pydantic import Field

from app.db.models import LogisticsBase
from app.platform.business import BusinessRuntime, StrictBody
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import authenticated_identity

shipments = LogisticsBase.metadata.tables["public.wf_e2e_shipments"]


class OrderRef(StrictBody):
    org_id: UUID
    order_id: UUID


class ChangeState(OrderRef):
    status: str = Field(pattern="^(HANDED_OVER|DELIVERED)$")


def create_app():
    runtime = BusinessRuntime("logistics", LogisticsBase.metadata.tables["public.command_receipts"])
    app = runtime.application()
    actor = Depends(authenticated_identity)

    @app.get("/api/v1/logistics/workflow/shipments/by-order/{order_id}")
    def get(order_id: UUID, org_id: UUID, identity=actor):
        runtime.allow(identity, org_id, "shipments.read")
        return runtime.fetch(shipments, org_id, sales_order_ref=order_id)

    @app.post("/api/v1/logistics/workflow/shipments", status_code=201)
    def create(
        body: OrderRef,
        identity=actor,
        authorization: str = Header(),
        idempotency_key: str = Header(default=""),
    ):
        runtime.allow(identity, body.org_id, "shipments.create")
        token = authorization.partition(" ")[2]
        reserved = runtime.remote(
            "warehouse",
            "GET",
            f"/api/v1/warehouse/workflow/reservations/{body.order_id}?org_id={body.org_id}",
            token,
        )
        dispatch = reserved.get("dispatch")
        if not dispatch or dispatch["status"] not in {"PREPARED", "HANDED_OVER"}:
            raise CoreError(ErrorCode.INVALID_STATE)

        def write(conn):
            conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                {"scope": f"shipment:{body.org_id}:{body.order_id}"},
            )
            existing = (
                conn.execute(
                    sa.select(shipments).where(
                        shipments.c.org_id == body.org_id,
                        shipments.c.sales_order_ref == body.order_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing:
                return dict(existing)
            return dict(
                conn.execute(
                    shipments.insert()
                    .values(
                        id=uuid4(),
                        org_id=body.org_id,
                        sales_order_ref=body.order_id,
                        warehouse_dispatch_ref=UUID(dispatch["id"]),
                        status="CREATED",
                        manifest={
                            "dispatch": dispatch,
                            "lines": reserved["lines"],
                            "warehouse_id": reserved["warehouse_id"],
                        },
                        event_id=uuid4(),
                        version=1,
                    )
                    .returning(shipments)
                )
                .mappings()
                .one()
            )

        return runtime.command(identity, body, "shipments.create", idempotency_key, write)

    @app.post("/api/v1/logistics/workflow/shipments/status")
    def status(
        body: ChangeState,
        identity=actor,
        authorization: str = Header(),
        idempotency_key: str = Header(default=""),
    ):
        runtime.allow(identity, body.org_id, "shipments.status")
        reserved = runtime.remote(
            "warehouse",
            "GET",
            f"/api/v1/warehouse/workflow/reservations/{body.order_id}?org_id={body.org_id}",
            authorization.partition(" ")[2],
        )
        dispatch = reserved.get("dispatch")
        if not dispatch or dispatch["status"] != "HANDED_OVER" or not dispatch["handoff_event_id"]:
            raise CoreError(ErrorCode.INVALID_STATE)

        def write(conn):
            row = runtime.one(
                conn, shipments, body.org_id, sales_order_ref=body.order_id, lock=True
            )
            if row["status"] == body.status:
                return row
            if (row["status"], body.status) not in {
                ("CREATED", "HANDED_OVER"),
                ("HANDED_OVER", "DELIVERED"),
            } or str(row["warehouse_dispatch_ref"]) != dispatch["id"]:
                raise CoreError(ErrorCode.INVALID_STATE)
            values = {
                "status": body.status,
                "updated_at": datetime.now(UTC),
                "version": row["version"] + 1,
                "handed_over_at": datetime.fromisoformat(
                    dispatch["handed_over_at"].replace("Z", "+00:00")
                ),
                "warehouse_handoff_event_ref": UUID(dispatch["handoff_event_id"]),
            }
            if body.status == "DELIVERED":
                values["delivered_at"] = datetime.now(UTC)
            return dict(
                conn.execute(
                    shipments.update()
                    .where(shipments.c.id == row["id"])
                    .values(**values)
                    .returning(shipments)
                )
                .mappings()
                .one()
            )

        return runtime.command(identity, body, "shipments.status", idempotency_key, write)

    from .operations import install

    install(app, runtime, {t.name: t for t in LogisticsBase.metadata.sorted_tables})
    return app
