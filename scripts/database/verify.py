"""Read-only reconciliation of the deterministic handoff demonstration.

Each database is read in its own consistent snapshot. This checks a quiescent
fixture set; it is not a distributed transaction or a live reconciliation worker.
No mutation, event processing, credentials or recipient PII is returned.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from contextlib import ExitStack
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from scripts.database.config import (
    layout,
    load_configuration,
    schema_for,
    validate_distinct_targets,
)
from scripts.database.engine import make_engine
from scripts.database.fixtures.business import ORG_ID
from scripts.database.registry import DATABASES, metadata_for

Row = Mapping[str, Any]
ZERO = Decimal("0.000")
READ_LIMIT = 20_000


def _require(condition: bool, message: str) -> None:
    if not condition:
        # Messages deliberately identify invariants, never SQL parameters or PII.
        raise RuntimeError(f"Demo verification failed: {message}.")


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        raise RuntimeError("Demo verification failed: invalid numeric quantity.") from None
    _require(result.is_finite(), "nonfinite numeric quantity")
    return result


def _key(row: Row, field: str = "id") -> tuple[UUID, UUID]:
    return row["org_id"], row[field]


def _index(rows: list[Row]) -> dict[tuple[UUID, UUID], Row]:
    result = {_key(row): row for row in rows}
    _require(len(result) == len(rows), "duplicate organization-scoped identity")
    return result


def _groups(rows: list[Row], parent: str) -> dict[tuple[UUID, UUID], list[Row]]:
    result: dict[tuple[UUID, UUID], list[Row]] = defaultdict(list)
    for row in rows:
        result[_key(row, parent)].append(row)
    return result


def _get(index: Mapping[tuple[UUID, UUID], Row], row: Row, field: str, label: str) -> Row:
    result = index.get(_key(row, field))
    _require(result is not None, f"missing or cross-organization {label}")
    assert result is not None
    return result


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _read(
    connections: Mapping[str, Connection],
) -> tuple[dict[str, dict[str, list[Row]]], dict[str, dict[str, int]]]:
    _require(
        set(connections) == set(DATABASES),
        "four domain connections are required",
    )
    data: dict[str, dict[str, list[Row]]] = {}
    counts: dict[str, dict[str, int]] = {}
    for database in DATABASES:
        connection = connections[database]
        metadata = metadata_for(database)
        existing = set(sa.inspect(connection).get_table_names(schema=schema_for(database)))
        expected = {table.name for table in metadata.tables.values()}
        _require(expected <= existing, f"{database} migrations are incomplete")
        counts[database] = {}
        if database == "core":
            for table in metadata.tables.values():
                counts[database][table.name] = int(
                    connection.scalar(sa.select(sa.func.count()).select_from(table)) or 0
                )
            continue
        data[database] = {}
        for table in metadata.sorted_tables:
            # Verify only the explicit demonstration organization. Other tenants'
            # data is neither required nor copied into this audit's output.
            rows = list(
                connection.execute(
                    sa.select(table).where(table.c.org_id == ORG_ID).limit(READ_LIMIT + 1)
                ).mappings()
            )
            _require(
                len(rows) <= READ_LIMIT,
                f"{database} fixture exceeds bounded verification limit",
            )
            data[database][table.name] = rows
            counts[database][table.name] = len(rows)
    _require(bool(data["sales"]["sales_orders"]), "business fixtures have not been seeded")
    return data, counts


def _verify_events(data: dict[str, dict[str, list[Row]]]) -> None:
    for database, tables in data.items():
        for event in tables["outbox_events"]:
            _require(
                _hash(event["payload"]) == event["payload_hash"],
                f"{database} outbox payload hash mismatch",
            )
        for receipt in tables["inbox_receipts"]:
            _require(
                _hash(receipt["payload"]) == receipt["payload_hash"],
                f"{database} inbox payload hash mismatch",
            )
            producer = data.get(receipt["producer_app"])
            _require(producer is not None, f"{database} fixture inbox has unknown producer")
            assert producer is not None
            events = _index(producer["outbox_events"])
            event = _get(events, receipt, "event_id", "source outbox event")
            for field in (
                "event_type",
                "event_schema_version",
                "aggregate_type",
                "aggregate_id",
                "aggregate_version",
                "payload_hash",
                "payload",
            ):
                _require(
                    receipt[field] == event[field],
                    f"{database} inbox/outbox envelope mismatch",
                )
            if receipt["status"] == "PROCESSED":
                _require(
                    event["status"] == "PUBLISHED",
                    f"{database} processed fixture event lacks accepted producer outcome",
                )


def _manifest(
    dispatch: Row,
    reservation: Row,
    lines: list[Row],
    reservation_lines: Mapping[tuple[UUID, UUID], Row],
    products: Mapping[tuple[UUID, UUID], Row],
) -> dict[str, Any]:
    manifest_lines = []
    for line in sorted(lines, key=lambda value: value["line_no"]):
        source = _get(reservation_lines, line, "reservation_line_id", "manifest reservation line")
        product = _get(products, source, "product_id", "manifest product")
        manifest_lines.append(
            {
                "dispatch_line_id": str(line["id"]),
                "reservation_line_id": str(source["id"]),
                "sales_order_line_ref": str(source["sales_order_line_ref"]),
                "product_ref": str(product["id"]),
                "sku": product["sku"],
                "uom": product["uom"],
                "quantity": format(_decimal(line["quantity"]), ".3f"),
            }
        )
    return {
        "org_id": str(dispatch["org_id"]),
        "warehouse_ref": str(reservation["warehouse_id"]),
        "dispatch_id": str(dispatch["id"]),
        "reservation_id": str(reservation["id"]),
        "sales_order_ref": str(reservation["sales_order_ref"]),
        "source_order_revision": reservation["source_order_revision"],
        "manifest_version": dispatch["manifest_version"],
        "lines": manifest_lines,
    }


def verify_connections(connections: Mapping[str, Connection]) -> dict[str, Any]:
    """SELECT-only fixture verification using caller-owned connections.

    Callers own transaction boundaries. ``verify_demo`` opens READ ONLY snapshots;
    integration tests may supply existing transactions to check corrupted rows.
    A result is returned only after all invariants pass.
    """
    data, counts = _read(connections)
    sales, warehouse, logistics = data["sales"], data["warehouse"], data["logistics"]
    products = _index(warehouse["products"])
    warehouses = _index(warehouse["warehouses"])
    orders = _index(sales["sales_orders"])
    order_lines = _index(sales["sales_order_lines"])
    lines_by_order = _groups(sales["sales_order_lines"], "order_id")
    reservations = _index(warehouse["reservations"])
    reservation_lines = _index(warehouse["reservation_lines"])
    lines_by_reservation = _groups(warehouse["reservation_lines"], "reservation_id")
    receipts = _index(warehouse["goods_receipts"])
    receipt_lines = _index(warehouse["goods_receipt_lines"])
    dispatches = _index(warehouse["dispatches"])
    dispatch_lines = _index(warehouse["dispatch_lines"])
    lines_by_dispatch = _groups(warehouse["dispatch_lines"], "dispatch_id")
    shipments = _index(logistics["shipments"])
    lines_by_shipment = _groups(logistics["shipment_lines"], "shipment_id")
    _verify_events(data)

    for line in sales["sales_order_lines"]:
        _get(orders, line, "order_id", "Sales line parent")
        product = _get(products, line, "product_ref", "Sales product reference")
        _require(
            line["sku"] == product["sku"] and line["uom"] == product["uom"],
            "Sales SKU/UOM does not match authoritative product",
        )
        _require(
            _decimal(line["ordered_quantity"]) > 0 and _decimal(line["unit_price"]) >= 0,
            "invalid Sales line quantity or price",
        )
    for order in orders.values():
        _require(bool(lines_by_order.get(_key(order))), "fixture Sales order has no lines")
        _get(warehouses, order, "fulfillment_warehouse_ref", "Sales selected warehouse")

    expected_holds: dict[tuple[UUID, UUID, UUID], Decimal] = defaultdict(lambda: ZERO)
    dispatched_counts: dict[tuple[UUID, UUID], Decimal] = defaultdict(lambda: ZERO)
    prepared_counts: dict[tuple[UUID, UUID], Decimal] = defaultdict(lambda: ZERO)
    handed_by_order_line: dict[tuple[UUID, UUID], Decimal] = defaultdict(lambda: ZERO)
    handoff_times: dict[tuple[UUID, UUID], list[Any]] = defaultdict(list)
    for reservation in reservations.values():
        order = _get(orders, reservation, "sales_order_ref", "reservation source Sales order")
        _get(warehouses, reservation, "warehouse_id", "reservation warehouse")
        _require(
            reservation["source_order_revision"] == order["input_revision"],
            "reservation source input revision mismatch",
        )
        _require(
            reservation["sales_order_number"] == order["order_number"],
            "reservation Sales order number mismatch",
        )
        _require(
            reservation["warehouse_id"] == order["fulfillment_warehouse_ref"],
            "reservation selected warehouse mismatch",
        )
        lines = lines_by_reservation.get(_key(reservation), [])
        if reservation["status"] == "CANCELLED" and not lines:
            _require(
                order["status"] == "CANCELLED",
                "zero-line cancellation fence lacks cancelled fixture order",
            )
            continue
        _require(bool(lines), "non-tombstone reservation has no lines")
        expected_source_lines = {line["id"] for line in lines_by_order[_key(order)]}
        _require(
            {line["sales_order_line_ref"] for line in lines} == expected_source_lines,
            "reservation allocation is not all-or-nothing",
        )
        for line in lines:
            source = _get(
                order_lines,
                line,
                "sales_order_line_ref",
                "reservation source Sales line",
            )
            _require(
                source["order_id"] == order["id"] and source["product_ref"] == line["product_id"],
                "reservation source line membership/product mismatch",
            )
            _require(
                _decimal(line["requested_quantity"])
                == _decimal(source["ordered_quantity"])
                == _decimal(line["allocated_quantity"]),
                "reservation quantity differs from frozen Sales input",
            )
            released, dispatched = (
                _decimal(line["released_quantity"]),
                _decimal(line["dispatched_quantity"]),
            )
            remaining = _decimal(line["allocated_quantity"]) - released - dispatched
            _require(
                released >= 0 and dispatched >= 0 and remaining >= 0,
                "invalid reservation quantity counters",
            )
            if reservation["status"] in (
                "RELEASED",
                "EXPIRED",
                "CANCELLED",
                "DISPATCHED",
            ):
                _require(remaining == 0, "terminal reservation retains a hold")
            if reservation["status"] == "HELD":
                _require(dispatched == 0, "HELD reservation contains dispatched quantity")
            expected_holds[(line["org_id"], reservation["warehouse_id"], line["product_id"])] += (
                remaining
            )

    manifest_by_dispatch: dict[tuple[UUID, UUID], dict[str, Any]] = {}
    for dispatch in dispatches.values():
        reservation = _get(reservations, dispatch, "reservation_id", "dispatch reservation")
        lines = lines_by_dispatch.get(_key(dispatch), [])
        if dispatch["status"] in ("PREPARED", "HANDED_OVER"):
            _require(bool(lines), "prepared dispatch has empty manifest")
        for line in lines:
            source = _get(
                reservation_lines,
                line,
                "reservation_line_id",
                "dispatch reservation line",
            )
            _require(
                line["reservation_id"] == reservation["id"] == source["reservation_id"],
                "dispatch line belongs to another reservation",
            )
            quantity = _decimal(line["quantity"])
            _require(quantity > 0, "dispatch line quantity is not positive")
            if dispatch["status"] == "PREPARED":
                prepared_counts[_key(source)] += quantity
            if dispatch["status"] == "HANDED_OVER":
                dispatched_counts[_key(source)] += quantity
                handed_by_order_line[_key(source, "sales_order_line_ref")] += quantity
        if dispatch["status"] in ("PREPARED", "HANDED_OVER"):
            manifest = _manifest(dispatch, reservation, lines, reservation_lines, products)
            _require(
                _hash(manifest) == dispatch["manifest_hash"],
                "Warehouse canonical manifest hash mismatch",
            )
            manifest_by_dispatch[_key(dispatch)] = manifest
        if dispatch["status"] == "HANDED_OVER":
            _require(
                dispatch["handed_over_at"] is not None,
                "Warehouse handoff lacks physical time",
            )
            handoff_times[_key(reservation, "sales_order_ref")].append(dispatch["handed_over_at"])
        if reservation["status"] in ("CANCELLED", "RELEASED", "EXPIRED"):
            _require(
                dispatch["status"] == "CANCELLED",
                "cancelled/released reservation retains live dispatch",
            )
    for line in reservation_lines.values():
        _require(
            dispatched_counts[_key(line)] == _decimal(line["dispatched_quantity"]),
            "reservation dispatched counter differs from physical handoff lines",
        )
        _require(
            prepared_counts[_key(line)]
            + dispatched_counts[_key(line)]
            + _decimal(line["released_quantity"])
            <= _decimal(line["allocated_quantity"]),
            "prepared manifests exceed remaining allocation",
        )

    ledger: dict[tuple[UUID, UUID, UUID], Decimal] = defaultdict(lambda: ZERO)
    movement_receipts: set[tuple[UUID, UUID]] = set()
    movement_dispatches: set[tuple[UUID, UUID]] = set()
    for movement in warehouse["stock_movements"]:
        _get(products, movement, "product_id", "stock movement product")
        _get(warehouses, movement, "warehouse_id", "stock movement warehouse")
        quantity = _decimal(movement["quantity_delta"])
        if movement["movement_type"] == "RECEIPT":
            source = _get(receipt_lines, movement, "goods_receipt_line_id", "stock receipt origin")
            receipt = _get(receipts, source, "receipt_id", "stock receipt document")
            _require(
                receipt["status"] == "POSTED"
                and quantity > 0
                and quantity == _decimal(source["accepted_quantity"]),
                "receipt ledger amount/status mismatch",
            )
            _require(
                source["product_id"] == movement["product_id"]
                and receipt["warehouse_id"] == movement["warehouse_id"],
                "receipt ledger product/warehouse mismatch",
            )
            _require(_key(source) not in movement_receipts, "duplicate stock receipt effect")
            movement_receipts.add(_key(source))
        elif movement["movement_type"] == "DISPATCH":
            source = _get(dispatch_lines, movement, "dispatch_line_id", "stock dispatch origin")
            dispatch = _get(dispatches, source, "dispatch_id", "stock dispatch document")
            reserved_line = _get(
                reservation_lines,
                source,
                "reservation_line_id",
                "stock dispatch reservation line",
            )
            reservation = _get(
                reservations, dispatch, "reservation_id", "stock dispatch reservation"
            )
            _require(
                dispatch["status"] == "HANDED_OVER"
                and quantity < 0
                and -quantity == _decimal(source["quantity"]),
                "dispatch ledger amount/status mismatch",
            )
            _require(
                reserved_line["product_id"] == movement["product_id"]
                and reservation["warehouse_id"] == movement["warehouse_id"],
                "dispatch ledger product/warehouse mismatch",
            )
            _require(
                movement["occurred_at"] == dispatch["handed_over_at"],
                "dispatch ledger physical time mismatch",
            )
            _require(
                _key(source) not in movement_dispatches,
                "duplicate stock dispatch effect",
            )
            movement_dispatches.add(_key(source))
        else:
            _require(False, "unknown stock movement origin")
        ledger[(movement["org_id"], movement["warehouse_id"], movement["product_id"])] += quantity
    for line in receipt_lines.values():
        receipt = _get(receipts, line, "receipt_id", "receipt line parent")
        accepted = _decimal(line["accepted_quantity"])
        _require(
            0 <= accepted <= _decimal(line["received_quantity"]),
            "receipt accepted amount exceeds physical quantity",
        )
        expected = receipt["status"] == "POSTED" and accepted > 0
        _require(
            (_key(line) in movement_receipts) == expected,
            "posted receipt line is not represented exactly once in ledger",
        )
    for line in dispatch_lines.values():
        dispatch = _get(dispatches, line, "dispatch_id", "dispatch line parent")
        _require(
            (_key(line) in movement_dispatches) == (dispatch["status"] == "HANDED_OVER"),
            "physical dispatch line is not represented exactly once in ledger",
        )

    inventory = []
    balance_keys = set()
    for balance in warehouse["inventory_balances"]:
        product = _get(products, balance, "product_id", "inventory product")
        _get(warehouses, balance, "warehouse_id", "inventory warehouse")
        key = balance["org_id"], balance["warehouse_id"], balance["product_id"]
        _require(key not in balance_keys, "duplicate warehouse/product balance")
        balance_keys.add(key)
        on_hand, reserved = (
            _decimal(balance["on_hand_quantity"]),
            _decimal(balance["reserved_quantity"]),
        )
        _require(on_hand == ledger[key], "on-hand balance differs from stock ledger")
        _require(
            reserved == expected_holds[key],
            "reserved balance differs from remaining reservation holds",
        )
        _require(on_hand >= reserved >= 0, "negative or over-reserved inventory")
        inventory.append(
            {
                "warehouse_id": str(balance["warehouse_id"]),
                "sku": product["sku"],
                "uom": product["uom"],
                "on_hand": format(on_hand, ".3f"),
                "reserved": format(reserved, ".3f"),
                "available": format(on_hand - reserved, ".3f"),
            }
        )
    _require(
        set(ledger) <= balance_keys and set(expected_holds) <= balance_keys,
        "ledger or hold lacks inventory balance",
    )

    warehouse_events = _index(warehouse["outbox_events"])
    for shipment in shipments.values():
        dispatch = _get(dispatches, shipment, "warehouse_dispatch_ref", "Logistics source dispatch")
        reservation = _get(reservations, dispatch, "reservation_id", "Logistics source reservation")
        order = _get(orders, shipment, "sales_order_ref", "Logistics source Sales order")
        _require(
            shipment["sales_order_ref"] == reservation["sales_order_ref"]
            and shipment["warehouse_ref"] == reservation["warehouse_id"],
            "Logistics source order/warehouse mismatch",
        )
        _require(
            shipment["sales_order_number"] == order["order_number"]
            and shipment["warehouse_dispatch_number"] == dispatch["dispatch_number"],
            "Logistics source document number mismatch",
        )
        _require(
            shipment["manifest_version"] == dispatch["manifest_version"]
            and shipment["manifest_hash"] == dispatch["manifest_hash"],
            "Logistics accepted another manifest",
        )
        _require(
            shipment["planned_handoff_at"] == order["requested_handoff_at"],
            "Logistics original handoff deadline mismatch",
        )
        lines = lines_by_shipment.get(_key(shipment), [])
        source_lines = lines_by_dispatch.get(_key(dispatch), [])
        _require(
            {line["warehouse_dispatch_line_ref"] for line in lines}
            == {line["id"] for line in source_lines},
            "Logistics manifest line set differs from Warehouse",
        )
        for line in lines:
            source = _get(
                dispatch_lines,
                line,
                "warehouse_dispatch_line_ref",
                "Logistics manifest line",
            )
            reserved_line = _get(
                reservation_lines,
                source,
                "reservation_line_id",
                "Logistics reservation line",
            )
            product = _get(products, reserved_line, "product_id", "Logistics product")
            _require(
                source["dispatch_id"] == dispatch["id"]
                and line["sales_order_line_ref"] == reserved_line["sales_order_line_ref"]
                and line["product_ref"] == product["id"],
                "Logistics line source membership/product mismatch",
            )
            _require(
                line["sku"] == product["sku"] and line["uom"] == product["uom"],
                "Logistics SKU/UOM mismatch",
            )
            _require(
                _decimal(line["planned_quantity"]) == _decimal(source["quantity"]),
                "Logistics planned manifest quantity mismatch",
            )
            expected_handed = (
                _decimal(line["planned_quantity"]) if shipment["status"] == "HANDED_OVER" else ZERO
            )
            _require(
                _decimal(line["handed_over_quantity"]) == expected_handed,
                "Logistics projected physical quantity mismatch",
            )
        if shipment["status"] in ("ACCEPTED", "HANDED_OVER"):
            _require(
                dispatch["logistics_shipment_ref"] == shipment["id"]
                and dispatch["logistics_acceptance_ref"] == shipment["acceptance_ref"],
                "Warehouse acceptance reference mismatch",
            )
            _require(
                dispatch["carrier_code_snapshot"] == shipment["carrier_code_snapshot"]
                and dispatch["carrier_name_snapshot"] == shipment["carrier_name_snapshot"],
                "carrier snapshot mismatch",
            )
        if shipment["status"] == "HANDED_OVER":
            _require(
                dispatch["status"] == "HANDED_OVER",
                "Logistics claims handoff without Warehouse physical evidence",
            )
            _require(
                shipment["handed_over_event_ref"] == dispatch["handoff_event_id"]
                and shipment["handed_over_at"] == dispatch["handed_over_at"],
                "Logistics physical event/time mismatch",
            )
            _require(
                shipment["handed_over_source_version"] == dispatch["version"],
                "Logistics handoff source version mismatch",
            )
    for dispatch in dispatches.values():
        if dispatch["status"] == "HANDED_OVER":
            shipment = _get(
                shipments,
                dispatch,
                "logistics_shipment_ref",
                "Warehouse accepted shipment",
            )
            _require(
                shipment["status"] == "HANDED_OVER"
                and shipment["warehouse_dispatch_ref"] == dispatch["id"],
                "fixture handoff has not been projected to matching Logistics shipment",
            )
            event = _get(
                warehouse_events,
                dispatch,
                "handoff_event_id",
                "Warehouse stable handoff event",
            )
            _require(
                event["aggregate_id"] == dispatch["id"]
                and event["aggregate_version"] == dispatch["version"],
                "Warehouse handoff event aggregate mismatch",
            )
            _require(
                event["payload"]["manifest_hash"] == dispatch["manifest_hash"],
                "Warehouse event manifest hash mismatch",
            )
            manifest = manifest_by_dispatch[_key(dispatch)]
            _require(
                all(event["payload"].get(key) == value for key, value in manifest.items()),
                "Warehouse event manifest body mismatch",
            )
            for consumer_name in ("sales", "logistics"):
                matches = [
                    receipt
                    for receipt in data[consumer_name]["inbox_receipts"]
                    if receipt["org_id"] == dispatch["org_id"]
                    and receipt["producer_app"] == "warehouse"
                    and receipt["event_id"] == event["id"]
                    and receipt["status"] == "PROCESSED"
                ]
                _require(
                    bool(matches),
                    f"{consumer_name} lacks processed Warehouse handoff evidence",
                )

    completed = []
    for order in orders.values():
        source_lines = lines_by_order[_key(order)]
        for line in source_lines:
            _require(
                handed_by_order_line[_key(line)] <= _decimal(line["ordered_quantity"]),
                "physical handoff exceeds ordered quantity",
            )
        if order["status"] == "COMPLETED":
            _require(
                all(
                    handed_by_order_line[_key(line)] == _decimal(line["ordered_quantity"])
                    for line in source_lines
                ),
                "Sales completion lacks full physical line handoff",
            )
            times = handoff_times[_key(order)]
            _require(
                bool(times) and order["completed_at"] == max(times),
                "Sales completed_at is not MAX verified physical handoff",
            )
            delay_seconds = max(
                Decimal(0),
                Decimal(
                    str((order["completed_at"] - order["requested_handoff_at"]).total_seconds())
                ),
            )
            completed.append(
                {
                    "order_number": order["order_number"],
                    "handed_dispatches": len(times),
                    "completed_at": order["completed_at"].isoformat(),
                    "delay_hours": format(delay_seconds / Decimal(3600), ".3f"),
                }
            )
        if order["status"] == "CANCELLED":
            _require(
                all(handed_by_order_line[_key(line)] == 0 for line in source_lines),
                "cancelled order has physical handoff",
            )

    _require(len(completed) >= 1, "completed fixture scenario is missing")
    _require(
        any(dispatch["status"] == "PREPARED" for dispatch in dispatches.values()),
        "prepared-not-handed fixture scenario is missing",
    )
    return {
        "status": "verified",
        "org_id": str(ORG_ID),
        "scope_end": "WAREHOUSE_PHYSICAL_HANDOFF_TO_CARRIER",
        "layout": layout(),
        "schemas": {domain: schema_for(domain) for domain in DATABASES},
        "consistent_read": (
            "Independent per-domain snapshots; quiescent fixture audit, not one shared snapshot"
        ),
        "rows": counts,
        "checks": [
            "same_org_opaque_references",
            "sku_uom_identity",
            "canonical_manifest_hashes",
            "inbox_outbox_evidence",
            "stock_ledger_origins",
            "on_hand_equals_ledger",
            "reserved_equals_remaining_holds",
            "dispatch_counters_and_prepared_caps",
            "logistics_matches_warehouse_evidence",
            "sales_completion_by_physical_lines",
        ],
        "inventory": sorted(inventory, key=lambda item: (item["warehouse_id"], item["sku"])),
        "completed_orders": sorted(completed, key=lambda item: item["order_number"]),
        "handed_over_dispatches": sum(
            dispatch["status"] == "HANDED_OVER" for dispatch in dispatches.values()
        ),
        "prepared_dispatches": sum(
            dispatch["status"] == "PREPARED" for dispatch in dispatches.values()
        ),
        "customer_delivery_claimed": False,
    }


def verify_demo() -> dict[str, Any]:
    """Audit configured isolated demo databases without writing any rows."""
    load_configuration()
    validate_distinct_targets()
    engines = {}
    try:
        with ExitStack() as stack:
            connections = {}
            for database in DATABASES:
                engine = make_engine(database, isolation_level="REPEATABLE READ")
                engines[database] = engine
                connection = stack.enter_context(engine.connect())
                stack.enter_context(connection.begin())
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connections[database] = connection
            return verify_connections(connections)
    finally:
        for engine in engines.values():
            engine.dispose()
