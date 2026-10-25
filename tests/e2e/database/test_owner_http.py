# SPDX-License-Identifier: GPL-3.0-or-later
"""Core HTTP action adapter -> real native owner handlers/SQL, with an IAM stub."""

import asyncio
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from scripts.business_actions import definitions
from scripts.database.registry import metadata_for
from tests.e2e.database.test_owner_operations import order_input, service
from tests.e2e.database.test_owner_operations import warehouse_owner as warehouse_owner

from app.platform.business import BusinessRuntime
from app.platform.http.correlation import CorrelationMiddleware
from app.platform.http.errors import CoreError, ErrorCode, register_exception_handlers
from app.platform.identity.authentication import VerifiedIdentity, authenticated_identity
from app.platform.workflow.actions import ActionDefinition, ActionRegistry
from app.platform.workflow.gateway import ActionGateway, AllowlistHttpPort

pytestmark = [pytest.mark.integration, pytest.mark.e2e, pytest.mark.asyncio]


async def test_native_owner_http_and_core_actions_finish_at_verified_carrier_handoff(
    warehouse_owner, databases
):
    owner = warehouse_owner
    order = order_input(owner, quantity=8)
    org, wh = owner.org, owner.wh
    tables = {
        domain: {t.name: t for t in metadata_for(domain).sorted_tables}
        for domain in ("sales", "warehouse", "logistics")
    }
    now = datetime.now(UTC)
    with databases["sales"].begin() as conn:
        customer = uuid4()
        conn.execute(
            tables["sales"]["customers"]
            .insert()
            .values(
                id=customer,
                org_id=org,
                customer_code="KH-ANPHAT",
                name="Công ty Thiết bị An Phát",
                status="ACTIVE",
                created_by_subject="sales-owner",
            )
        )
        conn.execute(
            tables["sales"]["sales_orders"]
            .insert()
            .values(
                id=UUID(order["id"]),
                org_id=org,
                customer_id=customer,
                order_number=order["order_number"],
                status="CONFIRMED",
                fulfillment_warehouse_ref=wh,
                input_revision=1,
                version=1,
                recipient_name=order["recipient_name"],
                recipient_phone=order["recipient_phone"],
                delivery_address_snapshot=order["delivery_address_snapshot"],
                requested_handoff_at=datetime.fromisoformat(order["requested_handoff_at"]),
                confirmed_at=owner.now,
                created_by_subject="sales-owner",
            )
        )
        line = order["lines"][0]
        conn.execute(
            tables["sales"]["sales_order_lines"]
            .insert()
            .values(
                id=UUID(line["id"]),
                org_id=org,
                order_id=UUID(order["id"]),
                line_no=1,
                product_ref=owner.product,
                sku=line["sku"],
                product_name_snapshot=line["product_name_snapshot"],
                uom=line["uom"],
                ordered_quantity=Decimal(line["ordered_quantity"]),
                unit_price=0,
            )
        )
    scheduled = now + timedelta(hours=2)
    with databases["logistics"].begin() as conn:
        conn.execute(
            tables["logistics"]["handoff_capacity_calendar"]
            .insert()
            .values(
                id=uuid4(),
                org_id=org,
                carrier_code="CARRIER-SOUTH",
                capacity_date=scheduled.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date(),
                max_dispatches=1,
                status="ACTIVE",
                updated_at=now,
            )
        )
    permissions = {
        "sales": {
            "warehouse-owner": ["operations.orders.read"],
            "logistics-owner": ["operations.orders.read"],
            "sales-owner": ["operations.orders.read", "operations.orders.reconcile"],
        },
        "warehouse": {
            "warehouse-owner": [
                "operations.inventory.read",
                "operations.inbound.read",
                "operations.receipts.post",
                "operations.stock.reserve",
                "operations.dispatch.prepare",
                "operations.dispatch.handoff",
                "operations.dispatch.read",
                "operations.handoff_proof.read",
            ],
            "sales-owner": ["operations.handoff_proof.read"],
            "logistics-owner": ["operations.dispatch.read", "operations.handoff_proof.read"],
        },
        "logistics": {
            "warehouse-owner": ["operations.shipments.read"],
            "logistics-owner": [
                "operations.shipments.book",
                "operations.shipments.reconcile",
                "operations.shipments.read",
            ],
        },
    }
    runtimes = {}
    clients = {}

    async def authenticated(request: Request):
        token = request.headers.get("Authorization", "").partition(" ")[2]
        if token not in {"sales-owner", "warehouse-owner", "logistics-owner", "it-admin"}:
            raise CoreError(ErrorCode.UNAUTHENTICATED)
        return VerifiedIdentity("https://identity.example.test", token)

    with ExitStack() as stack:
        for domain in ("sales", "warehouse", "logistics"):
            runtime = object.__new__(BusinessRuntime)
            runtime.engine, runtime.domain, runtime.receipts = (
                databases[domain],
                domain,
                tables[domain]["command_receipts"],
            )
            runtime.settings = SimpleNamespace(
                actor_bindings={
                    actor: {"org_id": str(org), "warehouse_ids": [str(wh)], "permissions": grants}
                    for actor, grants in permissions[domain].items()
                }
            )
            runtimes[domain] = runtime
            app = FastAPI()
            register_exception_handlers(app)
            app.add_middleware(CorrelationMiddleware)
            app.dependency_overrides[authenticated_identity] = authenticated
            service(domain).install(app, runtime, tables[domain])
            clients[domain] = stack.enter_context(TestClient(app))

        def remote(domain, method, path, token, payload=None, key=None):
            response = clients[domain].request(
                method,
                path,
                headers={
                    "Authorization": "Bearer " + token,
                    **({"Idempotency-Key": key} if key else {}),
                },
                json=payload,
            )
            if response.status_code not in {200, 201}:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            return response.json()

        for runtime in runtimes.values():
            runtime.remote = remote

        ports = {19084: "sales", 19085: "warehouse", 19086: "logistics"}

        async def native_http(request):
            response = await asyncio.to_thread(
                clients[ports[request.url.port]].request,
                request.method,
                request.url.raw_path.decode(),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(
                response.status_code,
                content=response.content,
                headers={"Content-Type": "application/json"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(native_http)) as http:
            registry = ActionRegistry()
            for definition in definitions()["actions"]:
                registry.register(ActionDefinition(**definition))
            gateway = ActionGateway(
                registry,
                http_port=AllowlistHttpPort(http, frozenset({"127.0.0.1"})),
                service_bases={
                    domain: "http://127.0.0.1:" + str(port) for port, domain in ports.items()
                },
            )

            async def action(identifier, body, role="warehouse-owner", key=None):
                return await gateway.execute(
                    identifier,
                    {"org_id": str(org), "warehouse_id": str(wh), **body},
                    actor=VerifiedIdentity("https://identity.example.test", role),
                    token=role,
                    idempotency_key=key,
                )

            receipt_body = {
                "inbound_plan_id": str(owner.plan),
                "expected_updated_at": owner.now.isoformat(),
                "receipt_number": "PN-2026-HTTP-01",
                "received_quantity": "10.000",
                "accepted_quantity": "10.000",
                "received_at": owner.now.isoformat(),
            }
            with pytest.raises(CoreError):
                await action(
                    "warehouse.operations.post_receipt", receipt_body, "it-admin", "admin-denied"
                )
            receipt = await action("warehouse.operations.post_receipt", receipt_body, key="receipt")
            assert receipt["accepted_quantity"] == "10.000"
            status = await action(
                "warehouse.operations.command_receipt_read",
                {"operation": "operations.receipts.post", "idempotency_key": "receipt"},
            )
            assert status["state"] == "SUCCEEDED" and status["outcome_certain"] is True
            assert status["resource_refs"]["receipt_id"] == receipt["receipt_id"]
            unknown = await action(
                "warehouse.operations.command_receipt_read",
                {"operation": "operations.receipts.post", "idempotency_key": "missing"},
            )
            assert unknown["state"] == "UNCONFIRMED" and unknown["outcome_certain"] is False
            another_warehouse = uuid4()
            runtimes["warehouse"].settings.actor_bindings["warehouse-owner"][
                "warehouse_ids"
            ].append(str(another_warehouse))
            foreign_scope = await action(
                "warehouse.operations.command_receipt_read",
                {
                    "warehouse_id": str(another_warehouse),
                    "operation": "operations.receipts.post",
                    "idempotency_key": "receipt",
                },
            )
            assert (
                foreign_scope["outcome_certain"] is False and foreign_scope["resource_refs"] == {}
            )
            inventory = await action("warehouse.operations.inventory_read", {})
            held = await action(
                "warehouse.operations.reserve_order",
                {
                    "order_id": order["id"],
                    "expected_order_revision": 1,
                    "inventory_versions": {str(owner.product): inventory["items"][0]["version"]},
                },
                key="reserve",
            )
            prepared = await action(
                "warehouse.operations.prepare_dispatch",
                {"order_id": order["id"], "expected_reservation_version": held["version"]},
                key="prepare",
            )
            proof = await action(
                "warehouse.operations.handoff_proof_read", {"order_id": order["id"]}
            )
            assert proof["complete"] is False
            booked = await action(
                "logistics.operations.book_carrier",
                {
                    "dispatch_id": prepared["dispatch"]["id"],
                    "expected_dispatch_version": 1,
                    "expected_manifest_hash": prepared["manifest_hash"],
                    "carrier_code": "CARRIER-SOUTH",
                    "carrier_name": "Vận tải Miền Nam",
                    "scheduled_handoff_at": scheduled.isoformat(),
                    "expected_capacity_updated_at": now.isoformat(),
                },
                "logistics-owner",
                "book",
            )
            assert booked["status"] == "ACCEPTED"
            await action(
                "warehouse.operations.record_carrier_handoff",
                {
                    "dispatch_id": prepared["dispatch"]["id"],
                    "shipment_id": booked["id"],
                    "expected_dispatch_version": 1,
                    "expected_manifest_hash": prepared["manifest_hash"],
                    "carrier_receiver_name": "Lê Văn Bình",
                    "carrier_receipt_number": "BG-2026-HTTP-01",
                    "handed_over_at": datetime.now(UTC).isoformat(),
                },
                key="handoff",
            )
            proof = await action(
                "warehouse.operations.handoff_proof_read", {"order_id": order["id"]}
            )
            assert proof["complete"] is True
            projected = await action(
                "logistics.operations.reconcile_handoff",
                {"shipment_id": booked["id"], "expected_version": 1},
                "logistics-owner",
                "shipment-reconcile",
            )
            assert projected["status"] == "HANDED_OVER"
            closed = await action(
                "sales.operations.reconcile_handoff",
                {"order_id": order["id"], "expected_version": 1},
                "sales-owner",
                "order-reconcile",
            )
            assert (
                closed["status"] == "COMPLETED" and closed["completed_at"] == proof["completed_at"]
            )
            assert (
                await action(
                    "sales.operations.reconcile_handoff",
                    {"order_id": order["id"], "expected_version": 1},
                    "sales-owner",
                    "order-reconcile",
                )
                == closed
            )
    with owner.engine.connect() as conn:
        assert (
            conn.scalar(
                sa.select(
                    sa.func.sum(tables["warehouse"]["stock_movements"].c.quantity_delta)
                ).where(tables["warehouse"]["stock_movements"].c.org_id == org)
            )
            == 2
        )
        assert (
            conn.scalar(
                sa.select(tables["warehouse"]["inventory_balances"].c.reserved_quantity).where(
                    tables["warehouse"]["inventory_balances"].c.org_id == org
                )
            )
            == 0
        )
