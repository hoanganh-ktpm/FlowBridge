"""Independent business invariant checks on persisted or generated domain snapshots."""

import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

from scripts.database.fixtures.business import VIETNAM, canonical_hash


def validate(rows, config):
    errors = defaultdict(list)

    def check(group, condition, record, reason):
        if not condition and len(errors[group]) < 12:
            errors[group].append(f"{record}: {reason}")

    def index(domain, table):
        return {r["id"]: r for r in rows[domain].get(table, [])}

    s, w, lg = (rows[d] for d in ("sales", "warehouse", "logistics"))
    orders, lines = index("sales", "sales_orders"), index("sales", "sales_order_lines")
    products, reservations = index("warehouse", "products"), index("warehouse", "reservations")
    rlines, dispatches = index("warehouse", "reservation_lines"), index("warehouse", "dispatches")
    dlines, receipts = index("warehouse", "dispatch_lines"), index("warehouse", "goods_receipts")
    glines, plans = index("warehouse", "goods_receipt_lines"), index("warehouse", "inbound_plans")
    shipments, evidence = index("logistics", "shipments"), index("warehouse", "outbox_events")
    orgs = {r["org_id"] for domain in rows.values() for records in domain.values() for r in records}
    check("references", len(orgs) == 1, "org_id", "expected exactly one synthetic organization")
    all_events = s.get("sales_order_events", []) + lg.get("shipment_events", [])
    for domain in rows.values():
        for table, records in domain.items():
            ids = [r["id"] for r in records]
            check("duplicates", len(ids) == len(set(ids)), table, "duplicate primary identity")
    logical = [(e.get("order_id", e.get("shipment_id")), e["event_type"]) for e in all_events]
    check("duplicates", len(logical) == len(set(logical)), "events", "duplicate logical transition")
    end = date.fromisoformat(config.reference_date)
    ledger, holds, receipts_by_plan = Counter(), Counter(), Counter()
    delivered = Counter()
    receipt_movement_counts, dispatch_movement_counts = Counter(), Counter()
    dispatch_quantities = Counter()
    order_handoffs = defaultdict(list)
    for dispatch in dispatches.values():
        reservation = reservations.get(dispatch["reservation_id"], {})
        if dispatch.get("handed_over_at"):
            order_handoffs[reservation.get("sales_order_ref")].append(dispatch["handed_over_at"])
    timeline = []
    for movement in w.get("stock_movements", []):
        pid, q = movement["product_id"], movement["quantity_delta"]
        ledger[pid] += q
        when = movement["occurred_at"]
        timeline.append(
            (when, 2, pid, q, q if movement["movement_type"] == "DISPATCH" else 0, movement["id"])
        )
        if movement["movement_type"] == "RECEIPT":
            receipt_movement_counts[movement["goods_receipt_line_id"]] += 1
            line = glines.get(movement["goods_receipt_line_id"], {})
            header = receipts.get(line.get("receipt_id"), {})
            check(
                "receipts",
                header.get("status") == "POSTED"
                and q == line.get("accepted_quantity")
                and when == header.get("posted_at"),
                movement["id"],
                "receipt movement must match POSTED accepted quantity/time",
            )
        else:
            dispatch_movement_counts[movement["dispatch_line_id"]] += 1
            dl = dlines.get(movement["dispatch_line_id"], {})
            dispatch = dispatches.get(dl.get("dispatch_id"), {})
            check(
                "handoff",
                dispatch.get("status") == "HANDED_OVER"
                and -q == dl.get("quantity")
                and when == dispatch.get("handed_over_at"),
                movement["id"],
                "dispatch ledger lacks matching physical evidence",
            )
    movement_sources = [
        (m.get("goods_receipt_line_id"), m.get("dispatch_line_id"))
        for m in w.get("stock_movements", [])
    ]
    check(
        "duplicates",
        len(movement_sources) == len(set(movement_sources)),
        "ledger",
        "duplicate movement origin",
    )
    for line in glines.values():
        header = receipts.get(line["receipt_id"], {})
        check(
            "receipts",
            receipt_movement_counts[line["id"]]
            == (1 if header.get("status") == "POSTED" and line["accepted_quantity"] > 0 else 0),
            line["id"],
            "POSTED receipt line must have exactly one matching stock movement",
        )
        pid = line.get("inbound_plan_id")
        if pid:
            plan = plans.get(pid, {})
            check(
                "inbound",
                plan.get("product_id") == line["product_id"]
                and plan.get("warehouse_id") == header.get("warehouse_id"),
                line["id"],
                "plan product/warehouse mismatch",
            )
            if header.get("status") == "POSTED" and plan:
                receipts_by_plan[pid] += line["accepted_quantity"]
                check(
                    "chronology",
                    plan["created_at"] <= header["received_at"] <= header["posted_at"],
                    line["id"],
                    "receipt precedes plan/arrival",
                )
    for pid, plan in plans.items():
        q = receipts_by_plan[pid]
        check(
            "inbound",
            0 <= q <= plan["planned_quantity"]
            and ((plan["status"] == "RECEIVED") == (q == plan["planned_quantity"])),
            pid,
            "planned/POSTED received quantity or status disagrees",
        )
    allocated_by_order = defaultdict(dict)
    for r in rlines.values():
        reservation = reservations.get(r["reservation_id"], {})
        line = lines.get(r["sales_order_line_ref"], {})
        q = r["allocated_quantity"] - r["released_quantity"] - r["dispatched_quantity"]
        holds[r["product_id"]] += q
        allocated_by_order[reservation.get("sales_order_ref")][line.get("id")] = r[
            "allocated_quantity"
        ]
        check(
            "reservations",
            r["allocated_quantity"] == r["requested_quantity"] == line.get("ordered_quantity")
            and reservation.get("sales_order_ref") == line.get("order_id"),
            r["id"],
            "reservation must preserve the complete original line",
        )
        check(
            "references",
            r["product_id"] == line.get("product_ref"),
            r["id"],
            "reservation product reference mismatch",
        )
        timeline.append((r["created_at"], 1, r["product_id"], 0, r["allocated_quantity"], r["id"]))
    order_lines = defaultdict(list)
    for line in lines.values():
        order_lines[line["order_id"]].append(line)
        product = products.get(line["product_ref"], {})
        check(
            "references",
            line["order_id"] in orders
            and (product.get("sku"), product.get("uom")) == (line["sku"], line["uom"]),
            line["id"],
            "dangling order/product or inconsistent SKU/UOM",
        )
    for oid, allocated in allocated_by_order.items():
        check(
            "reservations",
            allocated == {line["id"]: line["ordered_quantity"] for line in order_lines[oid]},
            oid,
            "partial order reservation",
        )
    reservation_order_ids = [r["sales_order_ref"] for r in reservations.values()]
    check(
        "duplicates",
        len(reservation_order_ids) == len(set(reservation_order_ids)),
        "reservations",
        "double-reserved order",
    )
    replay_stock, replay_hold = Counter(), Counter()
    for when, _, pid, stock_delta, hold_delta, rid in sorted(
        timeline, key=lambda x: (x[0], x[1], str(x[5]))
    ):
        replay_stock[pid] += stock_delta
        replay_hold[pid] += hold_delta
        check(
            "inventory_timeline",
            replay_stock[pid] >= replay_hold[pid] >= 0,
            rid,
            "historical on_hand >= reserved >= 0 violated",
        )
        check(
            "chronology",
            when.astimezone(VIETNAM).date() <= end,
            rid,
            "future ledger/reservation event",
        )
    for b in w.get("inventory_balances", []):
        pid = b["product_id"]
        check(
            "inventory",
            b["on_hand_quantity"] >= b["reserved_quantity"] >= 0,
            b["id"],
            "negative stock/over-reservation",
        )
        check(
            "ledger",
            b["on_hand_quantity"] == ledger[pid]
            and b["reserved_quantity"] == holds[pid]
            and replay_hold[pid] == holds[pid],
            b["id"],
            "balance differs from ledger/remaining reservation holds",
        )
    for dl in dlines.values():
        dispatch = dispatches.get(dl["dispatch_id"], {})
        rl = rlines.get(dl["reservation_line_id"], {})
        check(
            "references",
            dispatch.get("reservation_id") == rl.get("reservation_id") == dl["reservation_id"],
            dl["id"],
            "dispatch/reservation reference mismatch",
        )
        if dispatch.get("status") == "HANDED_OVER":
            delivered[rl.get("sales_order_line_ref")] += dl["quantity"]
            dispatch_quantities[dl["reservation_line_id"]] += dl["quantity"]
        check(
            "handoff",
            dispatch_movement_counts[dl["id"]]
            == (1 if dispatch.get("status") == "HANDED_OVER" else 0),
            dl["id"],
            "physical dispatch line must have exactly one stock movement",
        )
    for r in rlines.values():
        check(
            "reservations",
            dispatch_quantities[r["id"]] == r["dispatched_quantity"],
            r["id"],
            "reservation dispatched counter differs from physical dispatch lines",
        )
    calendar = {
        (c["carrier_code"], c["capacity_date"]): c for c in lg.get("handoff_capacity_calendar", [])
    }
    capacity_use = Counter()
    for shipment in shipments.values():
        dispatch = dispatches.get(shipment["warehouse_dispatch_ref"], {})
        order = orders.get(shipment["sales_order_ref"], {})
        check(
            "references",
            dispatch.get("logistics_shipment_ref") == shipment["id"]
            and shipment["planned_handoff_at"] == order.get("requested_handoff_at")
            and dispatch.get("manifest_hash") == shipment["manifest_hash"],
            shipment["id"],
            "manifest/deadline/reference mismatch",
        )
        when = shipment["scheduled_handoff_at"]
        if not order or not dispatch or when is None:
            check("references", False, shipment["id"], "dangling order/dispatch or missing booking")
            continue
        if shipment["accepted_at"] and shipment["status"] != "CANCELLED":
            actual = shipment.get("handed_over_at") or when
            capacity_use[shipment["carrier_code_snapshot"], actual.astimezone(VIETNAM).date()] += 1
        check(
            "chronology",
            order["confirmed_at"] <= dispatch["prepared_at"] <= shipment["accepted_at"] <= when,
            shipment["id"],
            "confirmation/preparation/acceptance/schedule out of order",
        )
        if shipment["status"] == "HANDED_OVER":
            ev = evidence.get(shipment.get("handed_over_event_ref"), {})
            check(
                "handoff",
                dispatch.get("status") == "HANDED_OVER"
                and dispatch.get("handoff_event_id") == ev.get("id")
                and ev.get("aggregate_id") == dispatch.get("id")
                and ev.get("occurred_at")
                == shipment["handed_over_at"]
                == dispatch.get("handed_over_at")
                and ev.get("payload_hash") == canonical_hash(ev.get("payload", {})),
                shipment["id"],
                "missing/mismatched Warehouse physical handoff evidence",
            )
    for key, use in capacity_use.items():
        cap = calendar.get(key, {})
        check(
            "capacity",
            cap.get("status") == "ACTIVE" and use <= cap.get("max_dispatches", -1),
            key,
            f"used {use} exceeds active calendar capacity {cap.get('max_dispatches')}",
        )
    for line in lg.get("shipment_lines", []):
        source = lines.get(line["sales_order_line_ref"], {})
        shipment = shipments.get(line["shipment_id"], {})
        check(
            "references",
            source.get("product_ref") == line["product_ref"]
            and source.get("sku") == line["sku"]
            and source.get("uom") == line["uom"],
            line["id"],
            "shipment SKU/UOM/reference mismatch",
        )
        check(
            "handoff",
            line["handed_over_quantity"]
            == (line["planned_quantity"] if shipment.get("status") == "HANDED_OVER" else 0),
            line["id"],
            "shipment line handoff quantity mismatch",
        )
    for order in orders.values():
        complete = bool(order_lines[order["id"]]) and all(
            delivered[line["id"]] == line["ordered_quantity"] for line in order_lines[order["id"]]
        )
        check(
            "sales_completion",
            (order["status"] == "COMPLETED") == complete,
            order["id"],
            "Sales completion differs from full Warehouse line handoff",
        )
        check(
            "chronology",
            order["created_at"] <= order["confirmed_at"] <= order["updated_at"],
            order["id"],
            "order timestamps out of order",
        )
        if complete:
            times = order_handoffs[order["id"]]
            check(
                "sales_completion",
                order["completed_at"] == max(times),
                order["id"],
                "completion time must equal latest physical handoff",
            )
    for ev in all_events:
        check(
            "chronology",
            ev["occurred_at"] <= ev["recorded_at"]
            and ev["occurred_at"].astimezone(VIETNAM).date() <= end,
            ev["id"],
            "event recorded before occurrence or after simulation end",
        )
        if ev["source_app"] == "warehouse":
            source = evidence.get(ev.get("source_event_id"), {})
            check(
                "handoff",
                source.get("occurred_at") == ev["occurred_at"]
                and source.get("payload") == ev["details"],
                ev["id"],
                "projection has no matching source event",
            )
    confirmed = Counter()
    for line in lines.values():
        if line["order_id"] not in orders:
            continue
        confirmed[
            line["sku"], orders[line["order_id"]]["confirmed_at"].astimezone(VIETNAM).date()
        ] += line["ordered_quantity"]
    pattern_status = "SKIP (requires 365 days)"
    if config.days >= 365:
        start = end - timedelta(days=config.days - 1)
        values = {
            sku: [float(confirmed[sku, start + timedelta(days=i)]) for i in range(config.days)]
            for sku in ("SKU-A", "SKU-B", "SKU-C", "SKU-D", "SKU-E", "SKU-F")
        }
        a, b, c, d, e, f = (values[k] for k in values)
        check(
            "patterns",
            statistics.mean(a[-90:]) > statistics.mean(a[:90]) * 1.3,
            "SKU-A",
            "upward trend missing",
        )
        weekly = [
            statistics.mean(
                a[i] for i in range(len(a)) if (start + timedelta(days=i)).weekday() == weekday
            )
            for weekday in range(7)
        ]
        check("patterns", max(weekly) > min(weekly) * 1.15, "SKU-A", "weekly seasonality missing")
        check("patterns", statistics.pstdev(b) > 0, "SKU-B", "demand noise missing")
        check(
            "patterns",
            0.65 < statistics.mean(b[-90:]) / max(0.01, statistics.mean(b[:90])) < 1.4,
            "SKU-B",
            "stable demand drift",
        )
        check(
            "patterns",
            statistics.mean(c[45:135]) > statistics.mean(c[225:315]) * 1.4,
            "SKU-C",
            "seasonal rise/fall missing",
        )
        check(
            "patterns",
            statistics.mean(d[-90:]) < statistics.mean(d[:90]) * 0.8,
            "SKU-D",
            "downward trend missing",
        )
        check(
            "patterns",
            sum(v == 0 for v in e) > len(e) * 0.5,
            "SKU-E",
            "intermittent zero days missing",
        )
        check("patterns", max(f) > max(1, statistics.median(f)) * 4, "SKU-F", "spike missing")
        pattern_status = "FAIL" if errors["patterns"] else "PASS"
    events_by_order = defaultdict(list)
    for ev in s.get("sales_order_events", []):
        events_by_order[ev["order_id"]].append(ev)
    late_orders = [
        o
        for o in orders.values()
        if o.get("completed_at") and o["completed_at"] > o["requested_handoff_at"]
    ]
    waiting_recovered = [
        o
        for o in orders.values()
        if o["status"] == "COMPLETED"
        and any(e["to_status"] == "WAITING_STOCK" for e in events_by_order[o["id"]])
    ]
    late_receipts = [
        line
        for line in glines.values()
        if line.get("inbound_plan_id") in plans
        and line["receipt_id"] in receipts
        and receipts[line["receipt_id"]]["received_at"]
        > plans[line["inbound_plan_id"]]["expected_at"]
    ]
    scenarios = {
        "on_time": sum(
            bool(o.get("completed_at") and o["completed_at"] <= o["requested_handoff_at"])
            for o in orders.values()
        ),
        "late": len(late_orders),
        "late_inbound": len(late_receipts),
        "carrier_queue": sum(
            (sh["scheduled_handoff_at"].date() - sh["accepted_at"].date()).days > 2
            for sh in shipments.values()
            if sh.get("scheduled_handoff_at") and sh.get("accepted_at")
        ),
        "backlog_recovered": len(waiting_recovered),
        "pending": sum(o["status"] != "COMPLETED" for o in orders.values()),
    }
    for name, count in scenarios.items():
        check("scenarios", count > 0, name, "required business scenario absent")
    groups = (
        "inventory",
        "inventory_timeline",
        "ledger",
        "reservations",
        "references",
        "sales_completion",
        "handoff",
        "capacity",
        "chronology",
        "receipts",
        "inbound",
        "duplicates",
        "scenarios",
    )
    return {
        "valid": not any(errors.values()),
        "checks": {g: "FAIL" if errors[g] else "PASS" for g in groups},
        "patterns": pattern_status,
        "errors": {g: samples for g, samples in errors.items() if samples},
        "scenarios": scenarios,
        "rows": {d: {t: len(rs) for t, rs in tables.items()} for d, tables in rows.items()},
        "days": config.days,
        "skus": len(products),
    }
