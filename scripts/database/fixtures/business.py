"""Deterministic, internally reconciled business fixtures; never a live workflow.

Rows represent a fictional snapshot at 2026-10-20 15:00 Asia/Ho_Chi_Minh.
They are deliberately independent of ORM classes so migrations and fixture data
can be reviewed separately. The CLI owns insertion, transactions and replay.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

DEMO_NAMESPACE = UUID("32f7fbea-32a8-5b87-b251-73746dd79e93")
VIETNAM = timezone(timedelta(hours=7))


def demo_id(key: str) -> UUID:
    """Stable identifier shared with the Core fixture, without live DB lookup."""
    return uuid5(DEMO_NAMESPACE, key)


ORG_ID = demo_id("org:demo")
WAREHOUSE_ID = demo_id("warehouse:WH-HCM01")


def _time(day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=VIETNAM)


def _quantity(value: str | int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.001"))


def _money(value: str | int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def canonical_hash(payload: dict[str, Any]) -> str:
    """Fixture wire hash: sorted UTF-8 JSON keys with no insignificant spaces."""
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def business_seed_rows() -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Return fresh row dictionaries, in local foreign-key insertion order.

    This snapshot makes no distributed transaction claim. A bootstrap must run
    each database transaction separately and verify the complete fixture set.
    """
    sales = {
        name: []
        for name in (
            "customers",
            "sales_orders",
            "sales_order_lines",
            "sales_order_events",
            "command_receipts",
            "outbox_events",
            "inbox_receipts",
        )
    }
    warehouse = {
        name: []
        for name in (
            "products",
            "warehouses",
            "inventory_balances",
            "goods_receipts",
            "goods_receipt_lines",
            "reservations",
            "reservation_lines",
            "dispatches",
            "dispatch_lines",
            "stock_movements",
            "command_receipts",
            "outbox_events",
            "inbox_receipts",
        )
    }
    logistics = {
        name: []
        for name in (
            "shipments",
            "shipment_lines",
            "shipment_events",
            "command_receipts",
            "outbox_events",
            "inbox_receipts",
        )
    }
    dbs = {"sales": sales, "warehouse": warehouse, "logistics": logistics}

    def row(key: str, **values: Any) -> dict[str, Any]:
        return {"id": demo_id(key), "org_id": ORG_ID, **values}

    products = {
        "laptop": {
            "id": demo_id("product:LAP-DELL-3540"),
            "sku": "LAP-DELL-3540",
            "name": "Laptop Dell Latitude 3540, Core i5, RAM 16 GB, SSD 512 GB",
            "uom": "cái",
            "unit_cost": _money(14_500_000),
            "unit_price": _money(16_800_000),
        },
        "mouse": {
            "id": demo_id("product:MOU-LOGI-B100"),
            "sku": "MOU-LOGI-B100",
            "name": "Chuột Logitech B100 USB, màu đen",
            "uom": "cái",
            "unit_cost": _money(68_000),
            "unit_price": _money(95_000),
        },
        "paper": {
            "id": demo_id("product:PAP-DA-A4-80"),
            "sku": "PAP-DA-A4-80",
            "name": "Giấy Double A A4 80 gsm, 500 tờ/ram",
            "uom": "ram",
            "unit_cost": _money(72_000),
            "unit_price": _money(89_000),
        },
    }
    for product in products.values():
        warehouse["products"].append(
            {
                "id": product["id"],
                "org_id": ORG_ID,
                "sku": product["sku"],
                "name": product["name"],
                "uom": product["uom"],
                "unit_cost": product["unit_cost"],
                "currency": "VND",
                "status": "ACTIVE",
                "created_at": _time(14),
                "updated_at": _time(14),
            }
        )
    warehouse["warehouses"].append(
        row(
            "warehouse:WH-HCM01",
            warehouse_code="WH-HCM01",
            name="Kho Thành phố Hồ Chí Minh",
            address="Khu kho vận Linh Trung, Thành phố Hồ Chí Minh",
            status="ACTIVE",
            created_at=_time(14),
            updated_at=_time(14),
        )
    )
    customers = (
        (
            "KH-ANPHAT",
            "Công ty TNHH Thiết bị An Phát",
            "muahang@anphat.example",
            "0900000101",
        ),
        (
            "KH-MINHTAM",
            "Công ty TNHH Dịch vụ Minh Tâm",
            "vanphong@minhtam.example",
            "0900000102",
        ),
    )
    for code, name, email, phone in customers:
        sales["customers"].append(
            row(
                f"customer:{code}",
                customer_code=code,
                name=name,
                email=email,
                phone=phone,
                status="ACTIVE",
                created_by_subject="fixture:sales:lan",
                created_at=_time(14),
                updated_at=_time(14),
            )
        )

    orders: dict[str, dict[str, Any]] = {}
    lines: dict[tuple[str, str], dict[str, Any]] = {}

    def add_order(
        number: str,
        status: str,
        items: list[tuple[str, int]],
        *,
        created_at: datetime,
        confirmed_at: datetime,
        deadline: datetime,
        updated_at: datetime,
        customer: str = "KH-ANPHAT",
        version: int = 3,
        completed_at: datetime | None = None,
        cancelled_at: datetime | None = None,
    ) -> dict[str, Any]:
        order = row(
            f"order:{number}",
            order_number=number,
            customer_id=demo_id(f"customer:{customer}"),
            status=status,
            requested_handoff_at=deadline,
            recipient_name="Nguyễn Hà Linh" if customer == "KH-ANPHAT" else "Trần Quốc Bảo",
            recipient_phone="0900000201" if customer == "KH-ANPHAT" else "0900000202",
            delivery_address_snapshot=(
                "Văn phòng An Phát, phường Thủ Đức, Thành phố Hồ Chí Minh"
                if customer == "KH-ANPHAT"
                else "Văn phòng Minh Tâm, phường Tân Bình, Thành phố Hồ Chí Minh"
            ),
            delivery_instructions=(
                "Liên hệ người nhận trước khi giao; thông tin chỉ dùng lập nhãn vận chuyển."
            ),
            currency="VND",
            fulfillment_warehouse_ref=WAREHOUSE_ID,
            input_revision=1,
            workflow_instance_ref=None,
            version=version,
            created_by_subject="fixture:sales:lan",
            confirmed_at=confirmed_at,
            completed_at=completed_at,
            cancelled_at=cancelled_at,
            created_at=created_at,
            updated_at=updated_at,
        )
        sales["sales_orders"].append(order)
        orders[number] = order
        for index, (product_key, quantity) in enumerate(items, 1):
            product = products[product_key]
            item = row(
                f"order-line:{number}:{index}",
                order_id=order["id"],
                line_no=index,
                product_ref=product["id"],
                sku=product["sku"],
                product_name_snapshot=product["name"],
                uom=product["uom"],
                ordered_quantity=_quantity(quantity),
                unit_price=product["unit_price"],
                created_at=created_at,
            )
            sales["sales_order_lines"].append(item)
            lines[(number, product_key)] = item
        return order

    add_order(
        "SO-2026-0099",
        "RESERVED",
        [("laptop", 3), ("mouse", 5)],
        created_at=_time(15, 9),
        confirmed_at=_time(15, 9, 10),
        deadline=_time(23, 17),
        updated_at=_time(15, 9, 15),
        customer="KH-MINHTAM",
    )
    add_order(
        "SO-2026-0100",
        "COMPLETED",
        [("laptop", 10), ("mouse", 5)],
        created_at=_time(16, 8),
        confirmed_at=_time(16, 8, 10),
        deadline=_time(18, 17),
        updated_at=_time(19, 9, 2),
        completed_at=_time(19, 9),
        version=7,
    )
    add_order(
        "SO-2026-0101",
        "WAITING_STOCK",
        [("laptop", 4)],
        created_at=_time(20, 9),
        confirmed_at=_time(20, 9, 5),
        deadline=_time(23, 17),
        updated_at=_time(20, 9, 6),
        version=3,
    )
    add_order(
        "SO-2026-0102",
        "FULFILLING",
        [("paper", 12)],
        created_at=_time(20, 10),
        confirmed_at=_time(20, 10, 5),
        deadline=_time(22, 17),
        updated_at=_time(20, 11, 20),
        customer="KH-MINHTAM",
        version=4,
    )
    add_order(
        "SO-2026-0103",
        "CANCELLED",
        [("mouse", 2)],
        created_at=_time(20, 12),
        confirmed_at=_time(20, 12, 5),
        deadline=_time(24, 17),
        updated_at=_time(20, 12, 12),
        cancelled_at=_time(20, 12, 10),
        version=4,
    )

    def correlation(number: str) -> UUID:
        return demo_id(f"correlation:{number}")

    def command(
        database: str,
        operation: str,
        target: UUID,
        when: datetime,
        *,
        response: dict[str, Any],
        failed: bool = False,
        error_code: str | None = None,
        key_suffix: str = "1",
    ) -> None:
        key = f"{database}:{operation}:{target}:{key_suffix}"
        dbs[database]["command_receipts"].append(
            row(
                f"command:{key}",
                scope=f"{database}.{operation}:{target}",
                idempotency_key=f"fixture-{demo_id(key)}",
                actor_subject=f"fixture:{database}:operator",
                request_hash=canonical_hash(
                    {
                        "target_id": str(target),
                        "operation": operation,
                        "fixture_attempt": key_suffix,
                    }
                ),
                status="FAILED" if failed else "SUCCEEDED",
                resource_type=operation.split("_")[-1],
                resource_id=target,
                response_status=409 if failed else 200,
                response_body=response,
                error_code=error_code,
                created_at=when,
                completed_at=when + timedelta(seconds=1),
            )
        )

    def outbox(
        database: str,
        event_key: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_version: int,
        number: str,
        when: datetime,
        payload: dict[str, Any],
        *,
        published: bool = False,
    ) -> dict[str, Any]:
        event = row(
            f"event:{event_key}",
            event_type=event_type,
            event_schema_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            business_key=number,
            correlation_id=correlation(number),
            causation_id=None,
            payload=payload,
            payload_hash=canonical_hash(payload),
            status="PUBLISHED" if published else "PENDING",
            attempt_count=1 if published else 0,
            next_attempt_at=None,
            locked_by=None,
            locked_until=None,
            last_error_code=None,
            occurred_at=when,
            published_at=when + timedelta(seconds=10) if published else None,
        )
        dbs[database]["outbox_events"].append(event)
        return event

    def inbox(database: str, producer: str, event: dict[str, Any], consumer: str) -> None:
        dbs[database]["inbox_receipts"].append(
            row(
                f"inbox:{database}:{event['id']}:{consumer}",
                producer_app=producer,
                event_id=event["id"],
                event_type=event["event_type"],
                event_schema_version=1,
                consumer=consumer,
                payload=event["payload"],
                payload_hash=event["payload_hash"],
                aggregate_type=event["aggregate_type"],
                aggregate_id=event["aggregate_id"],
                aggregate_version=event["aggregate_version"],
                status="PROCESSED",
                attempt_count=1,
                next_attempt_at=None,
                last_error_code=None,
                received_at=event["occurred_at"] + timedelta(seconds=2),
                processed_at=event["occurred_at"] + timedelta(seconds=3),
            )
        )

    def sales_event(
        number: str,
        key: str,
        event_type: str,
        when: datetime,
        from_status: str,
        to_status: str,
        *,
        details: dict[str, Any] | None = None,
        source: dict[str, Any] | None = None,
    ) -> None:
        sales["sales_order_events"].append(
            row(
                f"sales-event:{number}:{key}",
                order_id=orders[number]["id"],
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
                source_app="warehouse" if source else "sales",
                source_event_id=source["id"] if source else None,
                source_aggregate_version=source["aggregate_version"] if source else None,
                actor_subject=None if source else "fixture:sales:lan",
                correlation_id=correlation(number),
                details=details or {},
                occurred_at=when,
                recorded_at=when + timedelta(seconds=3) if source else when,
            )
        )

    for number, order in orders.items():
        sales_event(
            number,
            "confirmed",
            "sales.order_confirmed",
            order["confirmed_at"],
            "SUBMITTED",
            "CONFIRMED",
            details={"input_revision": 1},
        )
        command(
            "sales",
            "confirm_order",
            order["id"],
            order["confirmed_at"],
            response={
                "order_number": number,
                "input_revision": 1,
                "status": "CONFIRMED",
            },
        )

    # All stock enters through physical receiving documents and its on-hand ledger.
    def receive(
        number: str, source_type: str, when: datetime, items: list[tuple[str, int]]
    ) -> None:
        receipt_id = demo_id(f"receipt:{number}")
        warehouse["goods_receipts"].append(
            row(
                f"receipt:{number}",
                warehouse_id=WAREHOUSE_ID,
                receipt_number=number,
                source_type=source_type,
                source_document_ref=f"FIXTURE-{number}",
                status="POSTED",
                received_at=when,
                posted_at=when + timedelta(minutes=5),
                received_by_subject="fixture:warehouse:hai",
                notes="Số liệu nhập kho giả lập có đối chiếu theo từng dòng hàng.",
                version=2,
                created_at=when,
                updated_at=when + timedelta(minutes=5),
            )
        )
        for index, (product_key, quantity) in enumerate(items, 1):
            product = products[product_key]
            receipt_line_id = demo_id(f"receipt-line:{number}:{index}")
            warehouse["goods_receipt_lines"].append(
                row(
                    f"receipt-line:{number}:{index}",
                    receipt_id=receipt_id,
                    line_no=index,
                    product_id=product["id"],
                    received_quantity=_quantity(quantity),
                    accepted_quantity=_quantity(quantity),
                    unit_cost_snapshot=product["unit_cost"],
                    rejection_reason=None,
                    created_at=when,
                )
            )
            warehouse["stock_movements"].append(
                row(
                    f"movement:receipt:{number}:{index}",
                    warehouse_id=WAREHOUSE_ID,
                    product_id=product["id"],
                    movement_type="RECEIPT",
                    quantity_delta=_quantity(quantity),
                    goods_receipt_line_id=receipt_line_id,
                    dispatch_line_id=None,
                    actor_subject="fixture:warehouse:hai",
                    correlation_id=correlation(number),
                    occurred_at=when,
                    recorded_at=when + timedelta(minutes=5),
                )
            )
        command(
            "warehouse",
            "post_receipt",
            receipt_id,
            when + timedelta(minutes=5),
            response={"receipt_number": number, "status": "POSTED"},
        )

    receive(
        "GR-2026-0001",
        "OPENING_BALANCE",
        _time(15, 7),
        [("laptop", 8), ("mouse", 25), ("paper", 50)],
    )
    receive("GR-2026-0002", "REPLENISHMENT", _time(17, 8), [("laptop", 7)])

    reservation_by_order: dict[str, dict[str, Any]] = {}
    reservation_lines: dict[tuple[str, str], dict[str, Any]] = {}

    def reserve(
        number: str,
        reservation_number: str,
        when: datetime,
        status: str,
        updated_at: datetime,
        *,
        tombstone: bool = False,
    ) -> None:
        order = orders[number]
        reservation = row(
            f"reservation:{number}",
            reservation_number=reservation_number,
            sales_order_ref=order["id"],
            sales_order_number=number,
            source_order_revision=1,
            warehouse_id=WAREHOUSE_ID,
            status=status,
            expires_at=None,
            cancelled_at=when if tombstone else None,
            requested_by_subject="fixture:sales:lan",
            version=3 if status == "DISPATCHED" else 1,
            created_at=when,
            updated_at=updated_at,
        )
        warehouse["reservations"].append(reservation)
        reservation_by_order[number] = reservation
        if not tombstone:
            for (line_number, product_key), line in lines.items():
                if line_number != number:
                    continue
                item = row(
                    f"reservation-line:{number}:{product_key}",
                    reservation_id=reservation["id"],
                    sales_order_line_ref=line["id"],
                    product_id=products[product_key]["id"],
                    requested_quantity=line["ordered_quantity"],
                    allocated_quantity=line["ordered_quantity"],
                    released_quantity=_quantity(0),
                    dispatched_quantity=line["ordered_quantity"]
                    if status == "DISPATCHED"
                    else _quantity(0),
                    created_at=when,
                    updated_at=updated_at,
                )
                warehouse["reservation_lines"].append(item)
                reservation_lines[(number, product_key)] = item
        command(
            "warehouse",
            "cancel_reservation" if tombstone else "reserve_order",
            order["id"],
            when,
            response={
                "reservation_id": str(reservation["id"]),
                "status": "CANCELLED" if tombstone else "HELD",
            },
        )

    reserve("SO-2026-0099", "RSV-2026-0099", _time(15, 9, 15), "HELD", _time(15, 9, 15))
    reserve("SO-2026-0100", "RSV-2026-0100", _time(17, 8, 10), "DISPATCHED", _time(19, 9))
    reserve("SO-2026-0102", "RSV-2026-0102", _time(20, 10, 10), "HELD", _time(20, 10, 10))
    reserve(
        "SO-2026-0103",
        "RSV-2026-0103",
        _time(20, 12, 10),
        "CANCELLED",
        _time(20, 12, 10),
        tombstone=True,
    )

    for number, when, requested, available in (
        ("SO-2026-0100", _time(16, 8, 11), 10, 5),
        ("SO-2026-0101", _time(20, 9, 6), 4, 2),
    ):
        command(
            "warehouse",
            "reserve_order",
            orders[number]["id"],
            when,
            failed=True,
            error_code="INSUFFICIENT_STOCK",
            key_suffix="insufficient",
            response={
                "code": "INSUFFICIENT_STOCK",
                "sku": products["laptop"]["sku"],
                "requested_quantity": str(_quantity(requested)),
                "available_quantity": str(_quantity(available)),
                "reservation_created": False,
            },
        )
        sales_event(
            number,
            "waiting",
            "warehouse.insufficient_stock",
            when,
            "CONFIRMED",
            "WAITING_STOCK",
            details={"sku": products["laptop"]["sku"], "allocated_quantity": "0.000"},
        )
    sales_event(
        "SO-2026-0099",
        "reserved",
        "sales.reservation_confirmed",
        _time(15, 9, 15),
        "CONFIRMED",
        "RESERVED",
    )
    sales_event(
        "SO-2026-0100",
        "reserved",
        "sales.reservation_confirmed",
        _time(17, 8, 10),
        "WAITING_STOCK",
        "RESERVED",
    )
    sales_event(
        "SO-2026-0102",
        "reserved",
        "sales.reservation_confirmed",
        _time(20, 10, 10),
        "CONFIRMED",
        "RESERVED",
    )
    sales_event(
        "SO-2026-0102",
        "prepared",
        "sales.fulfillment_prepared",
        _time(20, 11, 20),
        "RESERVED",
        "FULFILLING",
    )
    sales_event(
        "SO-2026-0103",
        "cancel-pending",
        "sales.cancellation_requested",
        _time(20, 12, 9),
        "CONFIRMED",
        "CANCEL_PENDING",
    )
    sales_event(
        "SO-2026-0103",
        "cancelled",
        "sales.cancellation_confirmed",
        _time(20, 12, 12),
        "CANCEL_PENDING",
        "CANCELLED",
        details={
            "reservation_id": str(reservation_by_order["SO-2026-0103"]["id"]),
            "zero_line_cancellation_fence": True,
        },
    )

    def dispatch(
        number: str,
        dispatch_number: str,
        shipment_number: str,
        items: list[tuple[str, int]],
        prepared_at: datetime,
        accepted_at: datetime,
        handed_over_at: datetime | None,
    ) -> None:
        order, reservation = orders[number], reservation_by_order[number]
        dispatch_id, shipment_id = (
            demo_id(f"dispatch:{dispatch_number}"),
            demo_id(f"shipment:{shipment_number}"),
        )
        acceptance_id = demo_id(f"acceptance:{shipment_number}")
        handoff_event_id = demo_id(f"event:handoff:{dispatch_number}") if handed_over_at else None
        manifest_lines = []
        for index, (product_key, quantity) in enumerate(items, 1):
            source_line, product = lines[(number, product_key)], products[product_key]
            dispatch_line_id = demo_id(f"dispatch-line:{dispatch_number}:{index}")
            manifest_lines.append(
                {
                    "dispatch_line_id": str(dispatch_line_id),
                    "reservation_line_id": str(reservation_lines[(number, product_key)]["id"]),
                    "sales_order_line_ref": str(source_line["id"]),
                    "product_ref": str(product["id"]),
                    "sku": product["sku"],
                    "uom": product["uom"],
                    "quantity": str(_quantity(quantity)),
                }
            )
            warehouse["dispatch_lines"].append(
                row(
                    f"dispatch-line:{dispatch_number}:{index}",
                    dispatch_id=dispatch_id,
                    reservation_id=reservation["id"],
                    reservation_line_id=reservation_lines[(number, product_key)]["id"],
                    line_no=index,
                    quantity=_quantity(quantity),
                    created_at=prepared_at,
                )
            )
            logistics["shipment_lines"].append(
                row(
                    f"shipment-line:{shipment_number}:{index}",
                    shipment_id=shipment_id,
                    line_no=index,
                    warehouse_dispatch_line_ref=dispatch_line_id,
                    sales_order_line_ref=source_line["id"],
                    product_ref=product["id"],
                    sku=product["sku"],
                    product_name_snapshot=product["name"],
                    uom=product["uom"],
                    planned_quantity=_quantity(quantity),
                    handed_over_quantity=_quantity(quantity) if handed_over_at else _quantity(0),
                    created_at=accepted_at,
                    updated_at=handed_over_at + timedelta(seconds=3)
                    if handed_over_at
                    else accepted_at,
                )
            )
            if handed_over_at:
                warehouse["stock_movements"].append(
                    row(
                        f"movement:dispatch:{dispatch_number}:{index}",
                        warehouse_id=WAREHOUSE_ID,
                        product_id=product["id"],
                        movement_type="DISPATCH",
                        quantity_delta=-_quantity(quantity),
                        goods_receipt_line_id=None,
                        dispatch_line_id=dispatch_line_id,
                        actor_subject="fixture:warehouse:hai",
                        correlation_id=correlation(number),
                        occurred_at=handed_over_at,
                        recorded_at=handed_over_at,
                    )
                )
        manifest = {
            "org_id": str(ORG_ID),
            "warehouse_ref": str(WAREHOUSE_ID),
            "dispatch_id": str(dispatch_id),
            "reservation_id": str(reservation["id"]),
            "sales_order_ref": str(order["id"]),
            "source_order_revision": 1,
            "manifest_version": 1,
            "lines": manifest_lines,
        }
        manifest_hash = canonical_hash(manifest)
        carrier_code, carrier_name = "GHN", "Giao Hàng Nhanh"
        warehouse["dispatches"].append(
            row(
                f"dispatch:{dispatch_number}",
                dispatch_number=dispatch_number,
                reservation_id=reservation["id"],
                status="HANDED_OVER" if handed_over_at else "PREPARED",
                manifest_version=1,
                manifest_hash=manifest_hash,
                logistics_shipment_ref=shipment_id,
                logistics_acceptance_ref=acceptance_id,
                carrier_code_snapshot=carrier_code,
                carrier_name_snapshot=carrier_name,
                carrier_receiver_name="Lê Văn Hùng" if handed_over_at else None,
                handoff_proof_document_ref=None,
                handoff_event_id=handoff_event_id,
                prepared_at=prepared_at,
                handed_over_at=handed_over_at,
                cancelled_at=None,
                prepared_by_subject="fixture:warehouse:hai",
                handed_over_by_subject="fixture:warehouse:hai" if handed_over_at else None,
                version=4 if handed_over_at else 3,
                created_at=prepared_at,
                updated_at=handed_over_at or accepted_at,
            )
        )
        logistics["shipments"].append(
            row(
                f"shipment:{shipment_number}",
                shipment_number=shipment_number,
                sales_order_ref=order["id"],
                sales_order_number=number,
                warehouse_ref=WAREHOUSE_ID,
                warehouse_dispatch_ref=dispatch_id,
                warehouse_dispatch_number=dispatch_number,
                manifest_version=1,
                manifest_hash=manifest_hash,
                carrier_code_snapshot=carrier_code,
                carrier_name_snapshot=carrier_name,
                tracking_number=f"FIXTURE-{shipment_number}",
                recipient_name_snapshot=order["recipient_name"],
                recipient_phone_snapshot=order["recipient_phone"],
                delivery_address_snapshot=order["delivery_address_snapshot"],
                delivery_instructions_snapshot=order["delivery_instructions"],
                planned_handoff_at=order["requested_handoff_at"],
                status="HANDED_OVER" if handed_over_at else "ACCEPTED",
                acceptance_ref=acceptance_id,
                handed_over_event_ref=handoff_event_id,
                handed_over_source_version=4 if handed_over_at else None,
                accepted_at=accepted_at,
                handed_over_at=handed_over_at,
                cancelled_at=None,
                workflow_instance_ref=None,
                version=3 if handed_over_at else 2,
                created_by_subject="fixture:logistics:mai",
                created_at=accepted_at,
                updated_at=handed_over_at + timedelta(seconds=3) if handed_over_at else accepted_at,
            )
        )
        acceptance_payload = {
            "org_id": str(ORG_ID),
            "dispatch_ref": str(dispatch_id),
            "shipment_ref": str(shipment_id),
            "acceptance_ref": str(acceptance_id),
            "manifest_version": 1,
            "manifest_hash": manifest_hash,
            "carrier_code": carrier_code,
            "carrier_name": carrier_name,
        }
        accepted_event = outbox(
            "logistics",
            f"accepted:{shipment_number}",
            "logistics.manifest_accepted",
            "shipment",
            shipment_id,
            2,
            number,
            accepted_at,
            acceptance_payload,
            published=True,
        )
        inbox("warehouse", "logistics", accepted_event, "warehouse.logistics_acceptance")
        logistics["shipment_events"].append(
            row(
                f"shipment-event:{shipment_number}:accepted",
                shipment_id=shipment_id,
                event_type="logistics.manifest_accepted",
                from_status="DRAFT",
                to_status="ACCEPTED",
                source_app="logistics",
                source_event_id=None,
                source_aggregate_version=None,
                actor_subject="fixture:logistics:mai",
                details=acceptance_payload,
                correlation_id=correlation(number),
                occurred_at=accepted_at,
                recorded_at=accepted_at,
            )
        )
        command(
            "warehouse",
            "prepare_dispatch",
            dispatch_id,
            prepared_at,
            response={
                "dispatch_number": dispatch_number,
                "manifest_version": 1,
                "manifest_hash": manifest_hash,
            },
        )
        command(
            "logistics",
            "accept_manifest",
            dispatch_id,
            accepted_at,
            response=acceptance_payload,
        )
        if handed_over_at:
            payload = {
                **manifest,
                "dispatch_number": dispatch_number,
                "sales_order_number": number,
                "shipment_ref": str(shipment_id),
                "manifest_hash": manifest_hash,
                "carrier_code": carrier_code,
                "carrier_name": carrier_name,
                "handed_over_at": handed_over_at.isoformat(),
            }
            event = outbox(
                "warehouse",
                f"handoff:{dispatch_number}",
                "warehouse.dispatch_handed_over",
                "dispatch",
                dispatch_id,
                4,
                number,
                handed_over_at,
                payload,
                published=True,
            )
            inbox("sales", "warehouse", event, "sales.handoff_projection")
            inbox("logistics", "warehouse", event, "logistics.handoff_projection")
            logistics["shipment_events"].append(
                row(
                    f"shipment-event:{shipment_number}:handoff",
                    shipment_id=shipment_id,
                    event_type="warehouse.dispatch_handed_over",
                    from_status="ACCEPTED",
                    to_status="HANDED_OVER",
                    source_app="warehouse",
                    source_event_id=event["id"],
                    source_aggregate_version=4,
                    actor_subject=None,
                    details=payload,
                    correlation_id=correlation(number),
                    occurred_at=handed_over_at,
                    recorded_at=handed_over_at + timedelta(seconds=3),
                )
            )
            first = dispatch_number == "DSP-2026-0100-1"
            sales_event(
                number,
                f"handoff:{dispatch_number}",
                "warehouse.dispatch_handed_over",
                handed_over_at,
                "FULFILLING" if first else "PARTIALLY_HANDED_OVER",
                "PARTIALLY_HANDED_OVER" if first else "COMPLETED",
                details=payload,
                source=event,
            )
            command(
                "warehouse",
                "handoff_dispatch",
                dispatch_id,
                handed_over_at,
                response={
                    "dispatch_number": dispatch_number,
                    "status": "HANDED_OVER",
                    "event_id": str(event["id"]),
                },
            )
            outbox(
                "logistics",
                f"handoff-projected:{shipment_number}",
                "logistics.handoff_projected",
                "shipment",
                shipment_id,
                3,
                number,
                handed_over_at + timedelta(seconds=3),
                {
                    "shipment_ref": str(shipment_id),
                    "warehouse_handoff_event_ref": str(event["id"]),
                },
            )

    sales_event(
        "SO-2026-0100",
        "prepared",
        "sales.fulfillment_prepared",
        _time(18, 8),
        "RESERVED",
        "FULFILLING",
    )
    dispatch(
        "SO-2026-0100",
        "DSP-2026-0100-1",
        "SHP-2026-0100-1",
        [("laptop", 6), ("mouse", 5)],
        _time(18, 8),
        _time(18, 8, 15),
        _time(18, 10),
    )
    dispatch(
        "SO-2026-0100",
        "DSP-2026-0100-2",
        "SHP-2026-0100-2",
        [("laptop", 4)],
        _time(19, 8),
        _time(19, 8, 15),
        _time(19, 9),
    )
    dispatch(
        "SO-2026-0102",
        "DSP-2026-0102-1",
        "SHP-2026-0102-1",
        [("paper", 12)],
        _time(20, 11),
        _time(20, 11, 15),
        None,
    )

    outbox(
        "sales",
        "completed:SO-2026-0100",
        "sales.order_completed",
        "sales_order",
        orders["SO-2026-0100"]["id"],
        7,
        "SO-2026-0100",
        _time(19, 9, 2),
        {
            "order_ref": str(orders["SO-2026-0100"]["id"]),
            "order_number": "SO-2026-0100",
            "completed_at": _time(19, 9).isoformat(),
            "completion_basis": "WAREHOUSE_PHYSICAL_HANDOFF",
        },
    )

    # Derive the balance snapshot, rather than writing independent fixture totals.
    # A PREPARED/ACCEPTED manifest still holds stock and creates no movement.
    for product_key, product in products.items():
        on_hand = sum(
            (
                item["quantity_delta"]
                for item in warehouse["stock_movements"]
                if item["product_id"] == product["id"]
            ),
            _quantity(0),
        )
        held = sum(
            (
                item["allocated_quantity"] - item["released_quantity"] - item["dispatched_quantity"]
                for item in warehouse["reservation_lines"]
                if item["product_id"] == product["id"]
            ),
            _quantity(0),
        )
        warehouse["inventory_balances"].append(
            row(
                f"balance:WH-HCM01:{product_key}",
                warehouse_id=WAREHOUSE_ID,
                product_id=product["id"],
                on_hand_quantity=on_hand,
                reserved_quantity=held,
                version={"laptop": 6, "mouse": 4, "paper": 2}[product_key],
                updated_at=_time(20, 15),
            )
        )
    return dbs


def business_seed_summary() -> dict[str, Any]:
    """Machine-readable acceptance facts, including the unit and time boundary."""
    rows = business_seed_rows()
    return {
        "fixture": "dxos-business-handoff-v1",
        "org_id": str(ORG_ID),
        "snapshot_at": _time(20, 15).isoformat(),
        "rows": {
            database: {table: len(items) for table, items in tables.items()}
            for database, tables in rows.items()
        },
        "completed_order": "SO-2026-0100",
        "completed_order_dispatches": 2,
        "completed_order_delay_hours": 16,
        "waiting_stock_order": "SO-2026-0101",
        "accepted_but_not_handed_order": "SO-2026-0102",
        "cancelled_zero_line_reservation_order": "SO-2026-0103",
        "inventory": [
            {
                "sku": "LAP-DELL-3540",
                "uom": "cái",
                "on_hand": "5.000",
                "reserved": "3.000",
                "available": "2.000",
            },
            {
                "sku": "MOU-LOGI-B100",
                "uom": "cái",
                "on_hand": "20.000",
                "reserved": "5.000",
                "available": "15.000",
            },
            {
                "sku": "PAP-DA-A4-80",
                "uom": "ram",
                "on_hand": "50.000",
                "reserved": "12.000",
                "available": "38.000",
            },
        ],
        "scope_end": "WAREHOUSE_PHYSICAL_HANDOFF_TO_CARRIER",
        "live_services_executed": False,
    }
