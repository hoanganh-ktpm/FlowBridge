"""Deterministic offline timeline; independent domain payloads, never runtime DB joins.

Uses the existing fixture identity, quantity and canonical hashing conventions.
The snapshot stops at physical Warehouse handoff, not customer delivery.
"""

import math
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import yaml

from scripts.database.fixtures.business import VIETNAM, _quantity, canonical_hash, demo_id


@dataclass(frozen=True)
class SimulationConfig:
    seed: int = 20261024
    reference_date: str = "2026-10-20"
    scale: float = 1.0
    days: int = 365

    def __post_init__(self):
        date.fromisoformat(self.reference_date)
        if not 0.01 <= self.scale <= 10 or not 14 <= self.days <= 3650:
            raise ValueError("scale must be 0.01..10 and days 14..3650")


def poisson(rng, mean):
    # Exact small-rate Poisson via exponential arrival intervals; no numpy dependency.
    count, elapsed = 0, 0.0
    while True:
        elapsed += rng.expovariate(mean)
        if elapsed > 1:
            return count
        count += 1


def simulate(config=None):
    config = config or SimulationConfig()
    rng = random.Random(config.seed)
    profiles = yaml.safe_load(Path(__file__).with_name("synthetic_profiles.yaml").read_text())
    end = date.fromisoformat(config.reference_date)
    start = end - timedelta(days=config.days - 1)
    fingerprint = canonical_hash({**asdict(config), "profiles": profiles})

    def identity(key):
        return demo_id(f"simulation:{fingerprint}:{key}")

    org, wh = identity("org"), identity("warehouse")
    rows = {d: defaultdict(list) for d in ("sales", "warehouse", "logistics")}
    sales, warehouse, logistics = (rows[d] for d in rows)

    def at(day, hour=8, minute=0):
        return datetime.combine(day, time(hour, minute), tzinfo=VIETNAM)

    def row(domain, table, key, **values):
        result = {"id": identity(key), "org_id": org, **values}
        rows[domain][table].append(result)
        return result

    def event(domain, parent, kind, before, after, when, source=None):
        table, parent_key = (
            ("sales_order_events", "order_id")
            if domain == "sales"
            else ("shipment_events", "shipment_id")
        )
        return row(
            domain,
            table,
            f"event:{domain}:{parent['id']}:{kind}",
            **{parent_key: parent["id"]},
            event_type=kind,
            from_status=before,
            to_status=after,
            source_app="warehouse" if source else domain,
            source_event_id=source["id"] if source else None,
            source_aggregate_version=4 if source else None,
            actor_subject="synthetic-operator",
            correlation_id=parent["sales_order_ref"] if domain == "logistics" else parent["id"],
            details=source["payload"] if source else {},
            occurred_at=when,
            recorded_at=when,
        )

    created = at(start, 6)
    row(
        "warehouse",
        "warehouses",
        "warehouse",
        warehouse_code="DEMO-MAIN",
        name="Synthetic main warehouse",
        status="ACTIVE",
        created_at=created,
        updated_at=created,
    )
    for n in range(40):
        row(
            "sales",
            "customers",
            f"customer:{n}",
            customer_code=f"DEMO-{n:03}",
            name=f"Synthetic customer {n:03}",
            status="ACTIVE",
            created_by_subject="synthetic",
            created_at=created,
            updated_at=created,
        )
    products = {}
    for sku in profiles["products"]:
        products[sku] = row(
            "warehouse",
            "products",
            sku,
            sku=sku,
            name=f"Demo {sku}",
            uom="EA",
            unit_cost=_quantity(100),
            currency="VND",
            status="ACTIVE",
            created_at=created,
            updated_at=created,
        )
    stock, held = Counter(), Counter()
    pending, booked, plans = [], [], []
    capacities, used = {}, Counter()
    # Future bookings are plans; no future handoff events are emitted.
    for offset in range(config.days + 60):
        day = start + timedelta(days=offset)
        overload = int(config.days * 0.60) <= offset < int(config.days * 0.70)
        for carrier in ("CARRIER-1", "CARRIER-2"):
            cap = max(1, round((1 if overload else 12) * config.scale))
            if day.weekday() == 6:
                cap = max(1, cap // 2)
            capacities[carrier, day] = cap
            row(
                "logistics",
                "handoff_capacity_calendar",
                f"capacity:{carrier}:{day}",
                carrier_code=carrier,
                capacity_date=day,
                max_dispatches=cap,
                status="ACTIVE",
                created_at=created,
                updated_at=created,
            )

    def receipt(key, quantities, when, plan=None):
        header = row(
            "warehouse",
            "goods_receipts",
            f"receipt:{key}",
            warehouse_id=wh,
            receipt_number=f"GR-{key}",
            source_type="REPLENISHMENT" if plan else "OPENING_BALANCE",
            status="POSTED",
            received_at=when,
            posted_at=when + timedelta(minutes=5),
            received_by_subject="synthetic-warehouse",
            version=1,
            created_at=when,
            updated_at=when + timedelta(minutes=5),
        )
        for i, (sku, quantity) in enumerate(quantities.items(), 1):
            line = row(
                "warehouse",
                "goods_receipt_lines",
                f"receipt-line:{key}:{sku}",
                receipt_id=header["id"],
                line_no=i,
                product_id=products[sku]["id"],
                received_quantity=_quantity(quantity),
                accepted_quantity=_quantity(quantity),
                inbound_plan_id=plan["id"] if plan else None,
                created_at=when,
            )
            row(
                "warehouse",
                "stock_movements",
                f"receipt-movement:{key}:{sku}",
                warehouse_id=wh,
                product_id=products[sku]["id"],
                movement_type="RECEIPT",
                quantity_delta=_quantity(quantity),
                goods_receipt_line_id=line["id"],
                dispatch_line_id=None,
                actor_subject="synthetic-warehouse",
                correlation_id=header["id"],
                occurred_at=header["posted_at"],
                recorded_at=header["posted_at"],
            )
            stock[sku] += quantity
        if plan:
            plan.update(status="RECEIVED", updated_at=header["posted_at"])

    receipt(
        "OPENING",
        {
            sku: max(5, round(p["base"] * config.scale * 7))
            for sku, p in profiles["products"].items()
        },
        at(start, 6),
    )

    sequence = 0
    for offset in range(config.days):
        day = start + timedelta(days=offset)
        for plan, sku, arrival in plans:
            if arrival == day:
                receipt(
                    f"P{warehouse['inbound_plans'].index(plan):05}",
                    {sku: int(plan["planned_quantity"])},
                    at(day, 7),
                    plan,
                )
        chunks = []
        for sku, p in profiles["products"].items():
            phase = offset / max(config.days - 1, 1)
            trend = 1 + p["trend"] * phase
            season = 1 + p["weekly"] * math.cos(2 * math.pi * day.weekday() / 7)
            season *= 1 + p["seasonal"] * math.sin(2 * math.pi * offset / 365)
            effect = p.get("spike_multiplier", 1) if offset % 61 in (28, 29, 30) else 1
            expected = p["base"] * config.scale * trend * season * effect
            demand = 0 if rng.random() < p.get("zero_probability", 0) else poisson(rng, expected)
            while demand:
                q = min(demand, rng.randint(1, 7))
                chunks.append((sku, q))
                demand -= q
        rng.shuffle(chunks)
        while chunks:
            basket = [chunks.pop()]
            if chunks and rng.random() < 0.5:
                basket.append(chunks.pop())
            sequence += 1
            when = at(day, 8) + timedelta(seconds=sequence % 1800)
            order = row(
                "sales",
                "sales_orders",
                f"order:{sequence}",
                order_number=f"SO-{sequence:06}",
                customer_id=identity(f"customer:{rng.randrange(40)}"),
                status="CONFIRMED",
                requested_handoff_at=at(day + timedelta(days=2), 17),
                recipient_name="Synthetic recipient",
                recipient_phone="0900000000",
                delivery_address_snapshot="Synthetic demo address",
                currency="VND",
                fulfillment_warehouse_ref=wh,
                input_revision=1,
                version=1,
                created_by_subject="synthetic-sales",
                confirmed_at=when + timedelta(minutes=1),
                completed_at=None,
                cancelled_at=None,
                created_at=when,
                updated_at=when + timedelta(minutes=1),
            )
            lines = [
                row(
                    "sales",
                    "sales_order_lines",
                    f"order-line:{sequence}:{i}",
                    order_id=order["id"],
                    line_no=i,
                    product_ref=products[sku]["id"],
                    sku=sku,
                    product_name_snapshot=products[sku]["name"],
                    uom="EA",
                    ordered_quantity=_quantity(q),
                    unit_price=_quantity(150),
                    created_at=when,
                )
                for i, (sku, q) in enumerate(basket, 1)
            ]
            event(
                "sales",
                order,
                "sales.order_confirmed",
                "SUBMITTED",
                "CONFIRMED",
                order["confirmed_at"],
            )
            pending.append((order, lines))

        for order, lines in list(pending):
            needs = Counter()
            for line in lines:
                needs[line["sku"]] += int(line["ordered_quantity"])
            if any(stock[s] - held[s] < q for s, q in needs.items()):
                if order["status"] != "WAITING_STOCK":
                    event(
                        "sales",
                        order,
                        "warehouse.stock_unavailable",
                        "CONFIRMED",
                        "WAITING_STOCK",
                        at(day, 9),
                    )
                    order.update(status="WAITING_STOCK", updated_at=at(day, 9))
                continue
            pending.remove((order, lines))
            when = at(day, 9)
            number = order["order_number"][3:]
            reservation = row(
                "warehouse",
                "reservations",
                f"reserve:{number}",
                reservation_number=f"RSV-{number}",
                sales_order_ref=order["id"],
                sales_order_number=order["order_number"],
                source_order_revision=1,
                warehouse_id=wh,
                status="HELD",
                requested_by_subject="synthetic-sales",
                version=1,
                created_at=when,
                updated_at=when,
            )
            rlines = []
            for line in lines:
                rlines.append(
                    row(
                        "warehouse",
                        "reservation_lines",
                        f"reserve-line:{line['id']}",
                        reservation_id=reservation["id"],
                        sales_order_line_ref=line["id"],
                        product_id=line["product_ref"],
                        requested_quantity=line["ordered_quantity"],
                        allocated_quantity=line["ordered_quantity"],
                        released_quantity=_quantity(0),
                        dispatched_quantity=_quantity(0),
                        created_at=when,
                        updated_at=when,
                    )
                )
                held[line["sku"]] += int(line["ordered_quantity"])
            event("sales", order, "warehouse.reserved", order["status"], "RESERVED", when)
            event("sales", order, "warehouse.prepared", "RESERVED", "FULFILLING", at(day, 9, 10))
            order.update(status="FULFILLING", updated_at=at(day, 9, 10))
            manifest = {
                "org_id": str(org),
                "order_ref": str(order["id"]),
                "lines": [
                    {
                        "sales_order_line_ref": str(line["id"]),
                        "sku": line["sku"],
                        "quantity": str(line["ordered_quantity"]),
                        "uom": line["uom"],
                    }
                    for line in lines
                ],
            }
            dispatch = row(
                "warehouse",
                "dispatches",
                f"dispatch:{number}",
                dispatch_number=f"DSP-{number}",
                reservation_id=reservation["id"],
                status="PREPARED",
                manifest_version=1,
                manifest_hash=canonical_hash(manifest),
                prepared_at=at(day, 9, 10),
                prepared_by_subject="synthetic-warehouse",
                version=3,
                created_at=when,
                updated_at=at(day, 9, 10),
            )
            dlines = [
                row(
                    "warehouse",
                    "dispatch_lines",
                    f"dispatch-line:{line['id']}",
                    dispatch_id=dispatch["id"],
                    reservation_id=reservation["id"],
                    reservation_line_id=r["id"],
                    line_no=i,
                    quantity=line["ordered_quantity"],
                    created_at=at(day, 9, 10),
                )
                for i, (line, r) in enumerate(zip(lines, rlines, strict=True), 1)
            ]
            candidates = (
                (future, carrier)
                for i in range(1, 61)
                for future in [day + timedelta(days=i)]
                for carrier in ("CARRIER-1", "CARRIER-2")
            )
            slot, carrier = next((dt, c) for dt, c in candidates if used[c, dt] < capacities[c, dt])
            used[carrier, slot] += 1
            shipment = row(
                "logistics",
                "shipments",
                f"shipment:{number}",
                shipment_number=f"SHP-{number}",
                sales_order_ref=order["id"],
                sales_order_number=order["order_number"],
                warehouse_ref=wh,
                warehouse_dispatch_ref=dispatch["id"],
                warehouse_dispatch_number=dispatch["dispatch_number"],
                manifest_version=1,
                manifest_hash=dispatch["manifest_hash"],
                carrier_code_snapshot=carrier,
                carrier_name_snapshot=carrier,
                recipient_name_snapshot=order["recipient_name"],
                recipient_phone_snapshot=order["recipient_phone"],
                delivery_address_snapshot=order["delivery_address_snapshot"],
                planned_handoff_at=order["requested_handoff_at"],
                scheduled_handoff_at=at(slot, 10),
                status="ACCEPTED",
                acceptance_ref=identity(f"acceptance:{number}"),
                accepted_at=at(day, 9, 15),
                handed_over_at=None,
                cancelled_at=None,
                version=2,
                created_by_subject="synthetic-logistics",
                created_at=at(day, 9, 15),
                updated_at=at(day, 9, 15),
            )
            slines = [
                row(
                    "logistics",
                    "shipment_lines",
                    f"shipment-line:{line['id']}",
                    shipment_id=shipment["id"],
                    line_no=i,
                    warehouse_dispatch_line_ref=d["id"],
                    sales_order_line_ref=line["id"],
                    product_ref=line["product_ref"],
                    sku=line["sku"],
                    product_name_snapshot=line["product_name_snapshot"],
                    uom=line["uom"],
                    planned_quantity=line["ordered_quantity"],
                    handed_over_quantity=_quantity(0),
                    created_at=at(day, 9, 15),
                    updated_at=at(day, 9, 15),
                )
                for i, (line, d) in enumerate(zip(lines, dlines, strict=True), 1)
            ]
            dispatch.update(
                logistics_shipment_ref=shipment["id"],
                logistics_acceptance_ref=shipment["acceptance_ref"],
            )
            event(
                "logistics",
                shipment,
                "logistics.shipment_accepted",
                "DRAFT",
                "ACCEPTED",
                shipment["accepted_at"],
            )
            booked.append((order, reservation, rlines, dispatch, dlines, shipment, slines, lines))

        for booking in list(booked):
            order, reservation, rlines, dispatch, dlines, shipment, slines, lines = booking
            if shipment["scheduled_handoff_at"].date() != day:
                continue
            booked.remove(booking)
            when = shipment["scheduled_handoff_at"]
            payload = {
                "dispatch_ref": str(dispatch["id"]),
                "sales_order_ref": str(order["id"]),
                "handed_over_at": when.isoformat(),
                "lines": [
                    {
                        "sales_order_line_ref": str(line["id"]),
                        "quantity": str(line["ordered_quantity"]),
                    }
                    for line in lines
                ],
            }
            evidence = row(
                "warehouse",
                "outbox_events",
                f"handoff:{dispatch['id']}",
                event_type="warehouse.dispatch_handed_over",
                event_schema_version=1,
                aggregate_type="dispatch",
                aggregate_id=dispatch["id"],
                aggregate_version=4,
                business_key=order["order_number"],
                correlation_id=order["id"],
                payload=payload,
                payload_hash=canonical_hash(payload),
                status="PUBLISHED",
                attempt_count=1,
                occurred_at=when,
                published_at=when + timedelta(seconds=1),
            )
            dispatch.update(
                status="HANDED_OVER",
                carrier_code_snapshot=shipment["carrier_code_snapshot"],
                carrier_name_snapshot=shipment["carrier_name_snapshot"],
                carrier_receiver_name="Synthetic carrier receiver",
                handoff_event_id=evidence["id"],
                handed_over_at=when,
                handed_over_by_subject="synthetic-warehouse",
                version=4,
                updated_at=when,
            )
            shipment.update(
                status="HANDED_OVER",
                handed_over_event_ref=evidence["id"],
                handed_over_source_version=4,
                handed_over_at=when,
                version=3,
                updated_at=when + timedelta(seconds=2),
            )
            reservation.update(status="DISPATCHED", version=2, updated_at=when)
            for line, r, d, sl in zip(lines, rlines, dlines, slines, strict=True):
                q = int(line["ordered_quantity"])
                held[line["sku"]] -= q
                stock[line["sku"]] -= q
                r.update(dispatched_quantity=line["ordered_quantity"], updated_at=when)
                sl.update(
                    handed_over_quantity=line["ordered_quantity"],
                    updated_at=when + timedelta(seconds=2),
                )
                row(
                    "warehouse",
                    "stock_movements",
                    f"dispatch-movement:{d['id']}",
                    warehouse_id=wh,
                    product_id=line["product_ref"],
                    movement_type="DISPATCH",
                    quantity_delta=-line["ordered_quantity"],
                    goods_receipt_line_id=None,
                    dispatch_line_id=d["id"],
                    actor_subject="synthetic-warehouse",
                    correlation_id=order["id"],
                    occurred_at=when,
                    recorded_at=when,
                )
            event(
                "logistics",
                shipment,
                "warehouse.dispatch_handed_over",
                "ACCEPTED",
                "HANDED_OVER",
                when,
                evidence,
            )
            event(
                "sales",
                order,
                "warehouse.dispatch_handed_over",
                "FULFILLING",
                "COMPLETED",
                when,
                evidence,
            )
            order.update(
                status="COMPLETED",
                completed_at=when,
                updated_at=when + timedelta(seconds=3),
                version=5,
            )

        for sku, p in profiles["products"].items():
            in_transit = sum(
                int(plan["planned_quantity"])
                for plan, s, arrival in plans
                if s == sku and arrival > day
            )
            backlog = sum(
                int(line["ordered_quantity"])
                for _, ls in pending
                for line in ls
                if line["sku"] == sku
            )
            if stock[sku] - held[sku] + in_transit < max(3, p["base"] * config.scale * 6) + backlog:
                quantity = max(5, round(p["base"] * config.scale * 12 + backlog))
                late = int(config.days * 0.30) <= offset < int(config.days * 0.40)
                arrival = day + timedelta(days=18 if late else 3)
                expected = at(day + timedelta(days=3), 7)
                plan = row(
                    "warehouse",
                    "inbound_plans",
                    f"plan:{day}:{sku}",
                    warehouse_id=wh,
                    product_id=products[sku]["id"],
                    planned_quantity=_quantity(quantity),
                    expected_at=expected,
                    status="APPROVED",
                    decision_case_ref=None,
                    created_at=at(day, 16),
                    updated_at=at(day, 16),
                )
                plans.append((plan, sku, arrival))

    for sku, p in products.items():
        row(
            "warehouse",
            "inventory_balances",
            f"balance:{sku}",
            warehouse_id=wh,
            product_id=p["id"],
            on_hand_quantity=_quantity(stock[sku]),
            reserved_quantity=_quantity(held[sku]),
            version=1,
            updated_at=at(end, 23),
        )
    return {d: dict(tables) for d, tables in rows.items()}
