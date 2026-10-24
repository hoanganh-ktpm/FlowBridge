# SPDX-License-Identifier: GPL-3.0-or-later
"""Sales–Warehouse adapter over published API facts, not source repositories."""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .analytics import fail, numeric


class SnapshotCache:
    """Bounded operational input cache; never caches authorization decisions."""

    def __init__(self, ttl=120, maximum=32):
        self.ttl, self.maximum, self.entries = ttl, maximum, {}

    @staticmethod
    def binding(ctx, request):
        return (
            str(ctx.org_id),
            str(ctx.owner_id),
            str(request.forecast_run_id),
            *(
                ref.model_dump_json() if ref else None
                for ref in (
                    request.stock,
                    request.inbound,
                    request.open_orders,
                    request.commitments,
                )
            ),
        )

    def put(self, ctx, request, value):
        now = monotonic()
        self.entries = {key: entry for key, entry in self.entries.items() if entry[0] > now}
        if len(self.entries) >= self.maximum:
            self.entries.pop(next(iter(self.entries)))
        identifier = str(uuid4())
        self.entries[identifier] = (now + self.ttl, self.binding(ctx, request), value)
        return identifier

    def get(self, ctx, request):
        entry = self.entries.get(str(request.snapshot_id))
        if entry is None or entry[0] <= monotonic() or entry[1] != self.binding(ctx, request):
            raise HTTPException(409, "Operational snapshot expired; refresh inputs")
        return entry[2]


def moment(value):
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if value.utcoffset() is None:
            fail("Planning timestamp requires timezone")
        return value.astimezone(UTC)
    except (TypeError, ValueError):
        fail("Invalid planning timestamp")


def known_orders(rows, commitments, warehouse_id, timezone):
    facts = defaultdict(lambda: {"dispatched": 0, "held": 0})
    measures = defaultdict(set)
    for row in commitments:
        if "warehouse_id" in row and str(row["warehouse_id"]) != str(warehouse_id):
            continue
        key = (str(row["order_id"]), str(row["order_line_id"]), row["sku"], row["uom"])
        dispatched, held = numeric(row["dispatched_quantity"]), numeric(row["held_quantity"])
        if min(dispatched, held) < 0:
            fail("Invalid order commitment quantities")
        facts[key]["dispatched"] += dispatched
        facts[key]["held"] += held
        measures[key[:2]].add(key[2:])
    orders, seen = {}, set()
    for row in rows:
        if "warehouse_id" not in row:
            fail("Open orders contract requires warehouse_id (Sales workspace view migration)")
        if str(row["warehouse_id"]) != str(warehouse_id):
            continue
        if row["status"] in {"COMPLETED", "CANCELLED"}:
            continue
        key = (str(row["order_id"]), str(row["order_line_id"]), row["sku"], row["uom"])
        if measures[key[:2]] and measures[key[:2]] != {key[2:]}:
            fail("Order/commitment SKU or UOM mismatch")
        if key in seen:
            fail("Duplicate order line in planning snapshot")
        seen.add(key)
        fact = facts[key]
        quantity = numeric(row["ordered_quantity"])
        remaining = quantity - fact["dispatched"] - fact["held"]
        if remaining < -0.000001:
            fail("Order fulfillment/hold facts exceed ordered quantity")
        if remaining <= 0:
            continue
        deadline = moment(row["requested_handoff_at"])
        order = orders.setdefault(
            key[0],
            {
                "id": key[0],
                "number": row.get("order_number", key[0]),
                "deadline": deadline,
                "lines": {},
            },
        )
        if order["deadline"] != deadline:
            fail("Inconsistent order deadline")
        measure = (row["sku"], row["uom"])
        order["lines"][measure] = order["lines"].get(measure, 0) + remaining
    return sorted(orders.values(), key=lambda order: (order["deadline"], order["id"]))


def project_orders(
    points,
    stocks,
    inbounds,
    orders,
    sku,
    unit,
    start,
    *,
    multiplier=1,
    delay=0,
    extra=0,
    arrival=None,
    timezone="Asia/Ho_Chi_Minh",
    snapshot_at=None,
):
    zone = ZoneInfo(timezone)
    snapshot_at = snapshot_at or datetime.combine(start, time.min, zone).astimezone(UTC)
    balances = {}
    for row in stocks:
        key = (row["sku"], row["uom"])
        if key in balances:
            fail("Ambiguous warehouse stock grain")
        value = numeric(row["on_hand_quantity"]) - numeric(row["reserved_quantity"])
        if value < 0:
            fail("Invalid available stock")
        balances[key] = value
    selected = (sku, unit)
    opening = balances.get(selected, 0)
    inbound_events = []
    for row in inbounds:
        if row["status"] not in {"APPROVED", "PARTIALLY_RECEIVED"}:
            continue
        at = moment(row["expected_at"])
        if at < snapshot_at:
            continue  # Unreceived overdue plans never become opening stock.
        quantity = numeric(row["remaining_quantity"])
        if quantity < 0:
            fail("Invalid inbound quantity")
        inbound_events.append((at + timedelta(days=delay), (row["sku"], row["uom"]), quantity))
    if arrival:
        at = max(datetime.combine(arrival, time.min, zone).astimezone(UTC), snapshot_at)
        inbound_events.append((at, selected, extra))
    pending, risk, finished, introduced = [], set(), set(), set()
    shortages, filled_at = {}, {}
    new_backlog, demand_total, served_new, selected_order_demand = 0, 0, 0, 0
    supply_total, peak, first_risk, series = opening, 0, None, []
    for point in points:
        day = date.fromisoformat(point["timestamp"])
        beginning = max(datetime.combine(day, time.min, zone).astimezone(UTC), snapshot_at)
        ending = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
        events = [
            (at, 0, "inbound", (key, quantity))
            for at, key, quantity in inbound_events
            if beginning <= at < ending
        ]
        for order in orders:
            if order["id"] in introduced:
                continue
            deadline = order["deadline"]
            if deadline < ending:
                events.append((max(deadline, beginning), 1, "order", order))
        # Daily forecast demand has no order timestamps; assess it only at day end.
        events.append((ending, 2, "forecast", point["p50"] * multiplier))
        events.sort(key=lambda event: (event[0], event[1]))
        selected_inbound, served_known = 0, 0
        previous_known_demand, served_today = selected_order_demand, 0
        for _at, _, kind, payload in events:
            due = None
            if kind == "inbound":
                key, quantity = payload
                balances[key] = balances.get(key, 0) + quantity
                if key == selected:
                    selected_inbound += quantity
                    supply_total += quantity
            elif kind == "order":
                due = payload
                introduced.add(due["id"])
                selected_order_demand += due["lines"].get(selected, 0)
                pending.append(due)
                if due["deadline"] < snapshot_at:
                    risk.add(due["id"])
            for order in pending:
                if order["id"] in finished:
                    continue
                if all(
                    balances.get(key, 0) + 1e-9 >= quantity
                    for key, quantity in order["lines"].items()
                ):
                    for key, quantity in order["lines"].items():
                        balances[key] -= quantity
                    served_known += order["lines"].get(selected, 0)
                    finished.add(order["id"])
                    filled_at[order["id"]] = _at.isoformat()
            if due is not None and due["id"] not in finished:
                risk.add(due["id"])
                shortages[due["id"]] = [
                    {
                        "sku": key[0],
                        "uom": key[1],
                        "missing_quantity": max(0, quantity - balances.get(key, 0)),
                    }
                    for key, quantity in due["lines"].items()
                    if quantity > balances.get(key, 0) + 1e-9
                ]
            if kind == "forecast":
                demand_total += payload
                new_backlog += payload
                served_today = min(new_backlog, balances.get(selected, 0))
                served_new += served_today
                balances[selected] = balances.get(selected, 0) - served_today
                new_backlog -= served_today
        position = supply_total - selected_order_demand - demand_total
        unmet_known = sum(o["lines"].get(selected, 0) for o in pending if o["id"] not in finished)
        shortage = unmet_known + new_backlog
        peak = max(peak, shortage)
        if (shortage > 1e-9 or risk) and first_risk is None:
            first_risk = day.isoformat()
        series.append(
            {
                "day": day.isoformat(),
                "demand": point["p50"] * multiplier,
                "inbound": selected_inbound,
                "net_position": position,
                "physical_inventory": max(0, balances.get(selected, 0)),
                "unmet_cumulative": shortage,
                "known_orders_at_risk": len(risk),
                "known_demand_due": selected_order_demand - previous_known_demand,
                "known_fulfilled_quantity": served_known,
                "forecast_fulfilled_quantity": served_today,
                "unmet_known_quantity": unmet_known,
                "unmet_forecast_quantity": new_backlog,
            }
        )
    return {
        "series": series,
        "orders": [
            {
                "order_id": o["id"],
                "order_number": o.get("number", o["id"]),
                "deadline": o["deadline"].isoformat(),
                "at_risk": o["id"] in risk,
                "fulfilled_at": filled_at.get(o["id"]),
                "shortages_at_deadline": shortages.get(o["id"], []),
                "already_overdue": o["deadline"] < snapshot_at,
                "lines": [
                    {"sku": key[0], "uom": key[1], "unallocated_quantity": quantity}
                    for key, quantity in o["lines"].items()
                ],
            }
            for o in orders
            if o["id"] in introduced
        ],
        "metrics": {
            "first_risk_date": first_risk,
            "peak_shortage": peak,
            "forecast_demand": demand_total,
            "fulfilled_quantity": served_new,
            "fulfillment_percent": 100 * served_new / demand_total if demand_total else None,
            "known_orders_at_risk": len(risk),
            "known_unallocated_demand": selected_order_demand,
        },
    }
