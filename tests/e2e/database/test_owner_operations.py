# SPDX-License-Identifier: GPL-3.0-or-later
"""Native owner transactions against disposable PostgreSQL, never the wf_e2e tables."""

import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from scripts.database.registry import metadata_for

from app.platform.business import BusinessRuntime
from app.platform.business_operations import EtaCommand, PreparationCommand
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity

pytestmark = [pytest.mark.integration, pytest.mark.e2e]
ROOT = Path(__file__).resolve().parents[3]


def service(name):
    key = "native_" + name + "_operations"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            key, ROOT / "services" / (name + "-api") / "app/operations.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


@pytest.fixture
def warehouse_owner(databases):
    engine = databases["warehouse"]
    tables = {t.name: t for t in metadata_for("warehouse").sorted_tables}
    org, wh, product, plan = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            tables["warehouses"]
            .insert()
            .values(
                id=wh,
                org_id=org,
                warehouse_code="WH-NATIVE",
                name="Kho Bình Dương",
                status="ACTIVE",
            )
        )
        conn.execute(
            tables["products"]
            .insert()
            .values(
                id=product,
                org_id=org,
                sku="COFFEE-500G",
                name="Cà phê rang mộc 500 g",
                uom="gói",
                status="ACTIVE",
            )
        )
        conn.execute(
            tables["inbound_plans"]
            .insert()
            .values(
                id=plan,
                org_id=org,
                warehouse_id=wh,
                product_id=product,
                planned_quantity=10,
                expected_at=now + timedelta(days=1),
                status="APPROVED",
                created_at=now - timedelta(days=1),
                updated_at=now,
            )
        )
    # Constructor DSN guards apply to deployed owner DB names. This fixture uses
    # the random test DB created by the existing administration fixture explicitly.
    runtime = object.__new__(BusinessRuntime)
    runtime.engine, runtime.domain, runtime.receipts = (
        engine,
        "warehouse",
        tables["command_receipts"],
    )
    runtime.settings = SimpleNamespace(
        actor_bindings={
            "warehouse-owner": {
                "org_id": str(org),
                "warehouse_ids": [str(wh)],
                "permissions": [
                    "operations.preparation.confirm",
                    "operations.inbound.update_eta",
                    "operations.receipts.post",
                    "operations.stock.reserve",
                    "operations.dispatch.prepare",
                    "operations.dispatch.handoff",
                ],
            }
        }
    )
    return SimpleNamespace(
        engine=engine,
        tables=tables,
        org=org,
        wh=wh,
        product=product,
        plan=plan,
        now=now,
        runtime=runtime,
        actor=VerifiedIdentity("https://identity.example.test", "warehouse-owner"),
    )


def test_preparation_and_eta_use_scoped_permissions_versions_and_atomic_outbox(warehouse_owner):
    owner = warehouse_owner
    operations = service("warehouse").WarehouseOperations(owner.runtime, owner.tables)
    body = PreparationCommand(
        org_id=owner.org, warehouse_id=owner.wh, preparation_minutes=0, expected_revision=0
    )
    first = operations.preparation(owner.actor, body, "policy")
    retry = operations.preparation(owner.actor, body, "policy")
    assert first == retry and first["preparation_policy_revision"] == 1
    assert first["preparation_confirmed_by"] == owner.actor.subject
    assert first["preparation_minutes"] == 0
    with pytest.raises(CoreError) as stale:
        operations.preparation(owner.actor, body, "stale-policy")
    assert stale.value.code == ErrorCode.VERSION_CONFLICT
    body = EtaCommand(
        org_id=owner.org,
        warehouse_id=owner.wh,
        inbound_plan_id=owner.plan,
        expected_updated_at=owner.now,
        expected_at=owner.now + timedelta(days=2),
    )
    updated = operations.eta(owner.actor, body, "eta")
    assert datetime.fromisoformat(updated["expected_at"]) == body.expected_at
    owner.runtime.settings.actor_bindings[owner.actor.subject]["warehouse_ids"] = []
    with pytest.raises(CoreError) as revoked:
        operations.eta(owner.actor, body, "eta")  # Even the cached receipt is private.
    assert revoked.value.code == ErrorCode.FORBIDDEN
    with owner.engine.connect() as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(owner.tables["outbox_events"])
                .where(owner.tables["outbox_events"].c.org_id == owner.org)
            )
            == 2
        )


def test_two_receipts_cannot_overreceive_and_rejected_goods_never_become_stock(warehouse_owner):
    owner = warehouse_owner
    module = service("warehouse")
    operations = module.WarehouseOperations(owner.runtime, owner.tables)
    base = dict(
        org_id=owner.org,
        warehouse_id=owner.wh,
        inbound_plan_id=owner.plan,
        expected_updated_at=owner.now,
        received_quantity=8,
        accepted_quantity=6,
        rejection_reason="Hai gói rách bao, cách ly không nhập tồn sử dụng.",
        received_at=owner.now,
    )
    bodies = [module.ReceiveCommand(**base, receipt_number="PN-2026-" + str(i)) for i in (1, 2)]

    def receive(i):
        try:
            return operations.receive(owner.actor, bodies[i], "receipt-" + str(i))
        except CoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(receive, (0, 1)))
    assert sum(isinstance(v, dict) for v in values) == 1
    assert ErrorCode.VERSION_CONFLICT in values
    winner = next(i for i, v in enumerate(values) if isinstance(v, dict))
    assert (
        operations.receive(owner.actor, bodies[winner], "receipt-" + str(winner)) == values[winner]
    )
    current = owner.runtime.fetch(owner.tables["inbound_plans"], owner.org, id=owner.plan)
    final = module.ReceiveCommand(
        **{
            **base,
            "expected_updated_at": current["updated_at"],
            "received_quantity": 5,
            "accepted_quantity": 4,
            "receipt_number": "PN-2026-03",
        }
    )
    operations.receive(owner.actor, final, "receipt-final")
    with owner.engine.connect() as conn:
        stock = (
            conn.execute(
                sa.select(owner.tables["inventory_balances"]).where(
                    owner.tables["inventory_balances"].c.org_id == owner.org
                )
            )
            .mappings()
            .one()
        )
        ledger = conn.scalar(
            sa.select(sa.func.sum(owner.tables["stock_movements"].c.quantity_delta)).where(
                owner.tables["stock_movements"].c.org_id == owner.org
            )
        )
        assert stock["on_hand_quantity"] == ledger == 10
        assert stock["reserved_quantity"] == 0
        assert (
            owner.runtime.one(conn, owner.tables["inbound_plans"], owner.org, id=owner.plan)[
                "status"
            ]
            == "RECEIVED"
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(owner.tables["goods_receipts"])
                .where(owner.tables["goods_receipts"].c.org_id == owner.org)
            )
            == 2
        )
        assert (
            conn.scalar(
                sa.select(
                    sa.func.sum(owner.tables["goods_receipt_lines"].c.received_quantity)
                ).where(owner.tables["goods_receipt_lines"].c.org_id == owner.org)
            )
            == 13
        )


def order_input(owner, quantity=8, product=None, suffix="01"):
    order_id, line_id = uuid4(), uuid4()
    return {
        "id": str(order_id),
        "org_id": str(owner.org),
        "fulfillment_warehouse_ref": str(owner.wh),
        "order_number": "DH-2026-" + suffix,
        "input_revision": 1,
        "status": "CONFIRMED",
        "confirmed_at": owner.now.isoformat(),
        "recipient_name": "Nguyễn Minh An",
        "recipient_phone": "0900000123",
        "delivery_address_snapshot": "Kho tiếp nhận Khu công nghiệp Sóng Thần, Bình Dương",
        "requested_handoff_at": (owner.now + timedelta(days=1)).isoformat(),
        "lines": [
            {
                "id": str(line_id),
                "org_id": str(owner.org),
                "order_id": str(order_id),
                "product_ref": str(product or owner.product),
                "sku": "COFFEE-500G",
                "product_name_snapshot": "Cà phê rang mộc 500 g",
                "uom": "gói",
                "ordered_quantity": str(quantity),
                "line_no": 1,
            }
        ],
    }


def ten_units(owner):
    module = service("warehouse")
    operations = module.WarehouseOperations(owner.runtime, owner.tables)
    body = module.ReceiveCommand(
        org_id=owner.org,
        warehouse_id=owner.wh,
        inbound_plan_id=owner.plan,
        expected_updated_at=owner.now,
        received_quantity=10,
        accepted_quantity=10,
        receipt_number="PN-INITIAL",
        received_at=owner.now,
    )
    operations.receive(owner.actor, body, "opening-replenishment")
    with owner.engine.connect() as conn:
        stock = (
            conn.execute(
                sa.select(owner.tables["inventory_balances"]).where(
                    owner.tables["inventory_balances"].c.org_id == owner.org
                )
            )
            .mappings()
            .one()
        )
    return operations, stock["version"]


def test_competing_orders_cannot_reserve_more_than_physical_stock(warehouse_owner):
    owner = warehouse_owner
    module = service("warehouse")
    operations, version = ten_units(owner)
    orders = [order_input(owner, suffix=str(i)) for i in (1, 2)]
    bodies = [
        module.ReserveCommand(
            org_id=owner.org,
            warehouse_id=owner.wh,
            order_id=order["id"],
            expected_order_revision=1,
            inventory_versions={owner.product: version},
        )
        for order in orders
    ]

    def reserve(i):
        try:
            return operations.reserve(owner.actor, bodies[i], "reserve-" + str(i), orders[i])
        except CoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, (0, 1)))
    assert ErrorCode.VERSION_CONFLICT in results
    winner = next(i for i, v in enumerate(results) if isinstance(v, dict))
    assert (
        operations.reserve(owner.actor, bodies[winner], "reserve-" + str(winner), orders[winner])
        == results[winner]
    )
    with owner.engine.connect() as conn:
        balance = (
            conn.execute(
                sa.select(owner.tables["inventory_balances"]).where(
                    owner.tables["inventory_balances"].c.org_id == owner.org
                )
            )
            .mappings()
            .one()
        )
        assert balance["reserved_quantity"] == 8
        assert balance["on_hand_quantity"] == 10
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(owner.tables["reservations"])
                .where(owner.tables["reservations"].c.org_id == owner.org)
            )
            == 1
        )
        assert (
            conn.scalar(
                sa.select(
                    sa.func.sum(owner.tables["reservation_lines"].c.allocated_quantity)
                ).where(owner.tables["reservation_lines"].c.org_id == owner.org)
            )
            == 8
        )


def test_missing_second_sku_rolls_back_whole_order_and_duplicate_sku_lines_stay_distinct(
    warehouse_owner,
):
    from copy import deepcopy

    owner = warehouse_owner
    module = service("warehouse")
    operations, version = ten_units(owner)
    order = order_input(owner, 4)
    missing_product = uuid4()
    order["lines"].append(
        {
            **order["lines"][0],
            "id": str(uuid4()),
            "product_ref": str(missing_product),
            "sku": "TEA-500G",
            "line_no": 2,
        }
    )
    body = module.ReserveCommand(
        org_id=owner.org,
        warehouse_id=owner.wh,
        order_id=order["id"],
        expected_order_revision=1,
        inventory_versions={owner.product: version, missing_product: 1},
    )
    with pytest.raises(CoreError):
        operations.reserve(owner.actor, body, "missing-second-sku", order)
    with owner.engine.connect() as conn:
        assert (
            conn.scalar(
                sa.select(owner.tables["inventory_balances"].c.reserved_quantity).where(
                    owner.tables["inventory_balances"].c.org_id == owner.org
                )
            )
            == 0
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(owner.tables["reservations"])
                .where(owner.tables["reservations"].c.org_id == owner.org)
            )
            == 0
        )
    # Two original lines of the same SKU must keep their Sales identities,
    # while inventory locking checks their combined quantity.
    order["lines"][1] = {**deepcopy(order["lines"][0]), "id": str(uuid4()), "line_no": 2}
    body = module.ReserveCommand(
        org_id=owner.org,
        warehouse_id=owner.wh,
        order_id=order["id"],
        expected_order_revision=1,
        inventory_versions={owner.product: version},
    )
    saved = operations.reserve(owner.actor, body, "same-product-two-lines", order)
    assert len(saved["lines"]) == 2
    assert {line["sales_order_line_ref"] for line in saved["lines"]} == {
        line["id"] for line in order["lines"]
    }
    assert sum(Decimal(line["allocated_quantity"]) for line in saved["lines"]) == 8


def prepare_order(owner, operations, order, stock_version):
    module = service("warehouse")
    body = module.ReserveCommand(
        org_id=owner.org,
        warehouse_id=owner.wh,
        order_id=order["id"],
        expected_order_revision=1,
        inventory_versions={owner.product: stock_version},
    )
    held = operations.reserve(owner.actor, body, "hold-" + order["id"], order)
    prepare = module.PrepareCommand(
        org_id=owner.org,
        warehouse_id=owner.wh,
        order_id=order["id"],
        expected_reservation_version=held["version"],
    )
    prepared = operations.prepare(owner.actor, prepare, "prepare-" + order["id"])
    assert operations.prepare(owner.actor, prepare, "prepare-" + order["id"]) == prepared
    assert prepared["dispatch"]["status"] == "PREPARED"
    return prepared


def test_real_carrier_slots_are_serialized_and_preparing_does_not_dispatch_stock(
    warehouse_owner, databases
):
    from app.platform.business_operations import canonical

    owner = warehouse_owner
    operations, version = ten_units(owner)
    orders = [order_input(owner, quantity=4, suffix=str(i)) for i in (1, 2)]
    sales_tables = {t.name: t for t in metadata_for("sales").sorted_tables}
    sales_engine = databases["sales"]
    with sales_engine.begin() as conn:
        customer = uuid4()
        conn.execute(
            sales_tables["customers"]
            .insert()
            .values(
                id=customer,
                org_id=owner.org,
                customer_code="KH-ANPHAT",
                name="Công ty Thiết bị An Phát",
                status="ACTIVE",
                created_by_subject="sales-owner",
            )
        )
        for order in orders:
            conn.execute(
                sales_tables["sales_orders"]
                .insert()
                .values(
                    id=UUID(order["id"]),
                    org_id=owner.org,
                    customer_id=customer,
                    order_number=order["order_number"],
                    status="CONFIRMED",
                    requested_handoff_at=datetime.fromisoformat(order["requested_handoff_at"]),
                    recipient_name=order["recipient_name"],
                    recipient_phone=order["recipient_phone"],
                    delivery_address_snapshot=order["delivery_address_snapshot"],
                    fulfillment_warehouse_ref=owner.wh,
                    input_revision=1,
                    version=1,
                    created_by_subject="sales-owner",
                    confirmed_at=owner.now,
                    created_at=owner.now,
                    updated_at=owner.now,
                )
            )
            for line in order["lines"]:
                conn.execute(
                    sales_tables["sales_order_lines"]
                    .insert()
                    .values(
                        id=UUID(line["id"]),
                        org_id=owner.org,
                        order_id=UUID(order["id"]),
                        line_no=line["line_no"],
                        product_ref=owner.product,
                        sku=line["sku"],
                        product_name_snapshot=line["product_name_snapshot"],
                        uom=line["uom"],
                        ordered_quantity=Decimal(line["ordered_quantity"]),
                        unit_price=0,
                        created_at=owner.now,
                    )
                )
    prepared = [
        prepare_order(owner, operations, order, version + i) for i, order in enumerate(orders)
    ]
    for value in prepared:
        assert canonical(value["manifest"]) == value["manifest_hash"]
        assert value["dispatch"]["handed_over_at"] is None
    with owner.engine.connect() as conn:
        balance = (
            conn.execute(
                sa.select(owner.tables["inventory_balances"]).where(
                    owner.tables["inventory_balances"].c.org_id == owner.org
                )
            )
            .mappings()
            .one()
        )
        assert balance["on_hand_quantity"] == 10 and balance["reserved_quantity"] == 8
        assert (
            conn.scalar(
                sa.select(sa.func.sum(owner.tables["stock_movements"].c.quantity_delta)).where(
                    owner.tables["stock_movements"].c.org_id == owner.org
                )
            )
            == 10
        )
    tables = {t.name: t for t in metadata_for("logistics").sorted_tables}
    engine = databases["logistics"]
    now = datetime.now(UTC)
    scheduled = now + timedelta(hours=3)
    from zoneinfo import ZoneInfo

    with engine.begin() as conn:
        conn.execute(
            tables["handoff_capacity_calendar"]
            .insert()
            .values(
                id=uuid4(),
                org_id=owner.org,
                carrier_code="CARRIER-SOUTH",
                capacity_date=scheduled.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date(),
                max_dispatches=1,
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            )
        )
    runtime = object.__new__(BusinessRuntime)
    runtime.engine, runtime.domain, runtime.receipts = (
        engine,
        "logistics",
        tables["command_receipts"],
    )
    runtime.settings = SimpleNamespace(
        actor_bindings={
            "logistics-owner": {
                "org_id": str(owner.org),
                "warehouse_ids": [str(owner.wh)],
                "permissions": ["operations.shipments.book", "operations.shipments.reconcile"],
            }
        }
    )
    actor = VerifiedIdentity("https://identity.example.test", "logistics-owner")
    module = service("logistics")
    logistics = module.LogisticsOperations(runtime, tables)
    bodies = [
        module.BookCommand(
            org_id=owner.org,
            warehouse_id=owner.wh,
            dispatch_id=p["dispatch"]["id"],
            expected_dispatch_version=1,
            expected_manifest_hash=p["manifest_hash"],
            carrier_code="CARRIER-SOUTH",
            carrier_name="Vận tải Miền Nam",
            scheduled_handoff_at=scheduled,
            expected_capacity_updated_at=now,
        )
        for p in prepared
    ]

    def book(i):
        try:
            return logistics.book(actor, bodies[i], "book-" + str(i), prepared[i], orders[i])
        except CoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(book, (0, 1)))
    assert sum(isinstance(v, dict) for v in values) == 1 and ErrorCode.VERSION_CONFLICT in values
    winner = next(i for i, v in enumerate(values) if isinstance(v, dict))
    assert (
        logistics.book(
            actor, bodies[winner], "book-" + str(winner), prepared[winner], orders[winner]
        )
        == values[winner]
    )
    assert values[winner]["status"] == "ACCEPTED" and values[winner]["handed_over_at"] is None
    with engine.connect() as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(tables["shipments"])
                .where(tables["shipments"].c.org_id == owner.org)
            )
            == 1
        )
        assert (
            conn.scalar(
                sa.select(sa.func.sum(tables["shipment_lines"].c.handed_over_quantity)).where(
                    tables["shipment_lines"].c.org_id == owner.org
                )
            )
            == 0
        )
    warehouse_module = service("warehouse")
    handoff = warehouse_module.HandoffCommand(
        org_id=owner.org,
        warehouse_id=owner.wh,
        dispatch_id=prepared[winner]["dispatch"]["id"],
        shipment_id=values[winner]["id"],
        expected_dispatch_version=1,
        expected_manifest_hash=prepared[winner]["manifest_hash"],
        carrier_receiver_name="Lê Văn Bình",
        carrier_receipt_number="BG-2026-001",
        handed_over_at=datetime.now(UTC),
    )
    with pytest.raises(CoreError) as future:
        operations.handoff(
            owner.actor,
            handoff.model_copy(update={"handed_over_at": datetime.now(UTC) + timedelta(days=1)}),
            "future-handoff",
            values[winner],
        )
    assert future.value.code == ErrorCode.VALIDATION_ERROR
    with pytest.raises(CoreError) as machine:
        operations.handoff(
            VerifiedIdentity(
                owner.actor.issuer, owner.actor.subject, client_id="worker", account_type="service"
            ),
            handoff,
            "machine-handoff",
            values[winner],
        )
    assert machine.value.code == ErrorCode.FORBIDDEN
    scope = SimpleNamespace(
        org_id=owner.org, warehouse_id=owner.wh, order_id=UUID(orders[winner]["id"])
    )
    with owner.engine.connect() as conn:
        assert operations.handoff_proof(conn, scope, orders[winner])["complete"] is False
    completed = operations.handoff(owner.actor, handoff, "actual-handoff", values[winner])
    assert completed["dispatch"]["status"] == "HANDED_OVER"
    assert operations.handoff(owner.actor, handoff, "actual-handoff", values[winner]) == completed
    with owner.engine.connect() as conn:
        proof = operations.handoff_proof(conn, scope, orders[winner])
        assert proof["complete"] and len(proof["evidence"]) == 1
        assert proof["completed_at"] == handoff.handed_over_at.isoformat()
        assert proof["lines"][0]["handed_over_quantity"] == "4.000"
        assert (
            owner.runtime.one(
                conn,
                owner.tables["inventory_balances"],
                owner.org,
                warehouse_id=owner.wh,
                product_id=owner.product,
            )["on_hand_quantity"]
            == 6
        )
        assert (
            conn.scalar(
                sa.select(sa.func.sum(owner.tables["stock_movements"].c.quantity_delta)).where(
                    owner.tables["stock_movements"].c.org_id == owner.org
                )
            )
            == 6
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(owner.tables["stock_movements"])
                .where(
                    owner.tables["stock_movements"].c.org_id == owner.org,
                    owner.tables["stock_movements"].c.movement_type == "DISPATCH",
                )
            )
            == 1
        )
        event = operations.handoff_event(conn, owner.org, owner.wh, handoff.dispatch_id)
    observed = module.ReconcileCommand(
        org_id=owner.org,
        warehouse_id=owner.wh,
        shipment_id=values[winner]["id"],
        expected_version=1,
    )
    projection = logistics.reconcile(actor, observed, "verify-handoff", completed, event)
    assert projection["status"] == "HANDED_OVER"
    assert projection["handed_over_event_ref"] == str(event["id"])
    assert logistics.reconcile(actor, observed, "verify-handoff", completed, event) == projection
    assert (
        logistics.reconcile(actor, observed, "verify-handoff-again", completed, event) == projection
    )
    with engine.connect() as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.sum(tables["shipment_lines"].c.handed_over_quantity)).where(
                    tables["shipment_lines"].c.org_id == owner.org
                )
            )
            == 4
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(tables["inbox_receipts"])
                .where(
                    tables["inbox_receipts"].c.org_id == owner.org,
                    tables["inbox_receipts"].c.status == "PROCESSED",
                )
            )
            == 1
        )
    sales_runtime = object.__new__(BusinessRuntime)
    sales_runtime.engine, sales_runtime.domain, sales_runtime.receipts = (
        sales_engine,
        "sales",
        sales_tables["command_receipts"],
    )
    sales_runtime.settings = SimpleNamespace(
        actor_bindings={
            "sales-owner": {
                "org_id": str(owner.org),
                "warehouse_ids": [str(owner.wh)],
                "permissions": ["operations.orders.reconcile"],
            }
        }
    )
    sales_actor = VerifiedIdentity("https://identity.example.test", "sales-owner")
    sales_module = service("sales")
    sales = sales_module.SalesOperations(sales_runtime, sales_tables)
    reconcile = sales_module.ReconcileOrder(
        org_id=owner.org, warehouse_id=owner.wh, order_id=orders[winner]["id"], expected_version=1
    )
    closed = sales.reconcile(sales_actor, reconcile, "order-close", proof, [event])
    assert (
        closed["status"] == "COMPLETED"
        and datetime.fromisoformat(closed["completed_at"]) == handoff.handed_over_at
    )
    assert sales.reconcile(sales_actor, reconcile, "order-close", proof, [event]) == closed
    assert sales.reconcile(sales_actor, reconcile, "order-close-again", proof, [event]) == closed
    with sales_engine.connect() as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(sales_tables["sales_order_events"])
                .where(sales_tables["sales_order_events"].c.org_id == owner.org)
            )
            == 1
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(sales_tables["inbox_receipts"])
                .where(
                    sales_tables["inbox_receipts"].c.org_id == owner.org,
                    sales_tables["inbox_receipts"].c.status == "PROCESSED",
                )
            )
            == 1
        )
