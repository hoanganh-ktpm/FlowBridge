# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned local demo profile; never resets existing organizations or uses hosted .env."""

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid5

from scripts.database.fixtures.business import VIETNAM, _quantity, canonical_hash
from scripts.database.simulation import SimulationConfig, simulate
from scripts.database.synthetic_validation import validate

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / ".tmp/decision-demo"
CATALOG = {
    "SKU-A": ("KB-K120", "Bàn phím Logitech K120 USB, màu đen"),
    "SKU-B": ("MS-B100", "Chuột Logitech B100 USB, màu đen"),
    "SKU-C": ("SW-SG108", "Switch TP-Link TL-SG108, 8 cổng Gigabit"),
    "SKU-D": ("USB-32GB", "USB Kingston DataTraveler Exodia, 32 GB"),
    "SKU-E": ("CAM-C270", "Webcam Logitech C270 HD 720p"),
    "SKU-F": ("HEAD-H111", "Tai nghe Logitech H111, jack 3,5 mm"),
    "SKU-G": ("PAD-MP01", "Tấm lót chuột vải, 25 × 20 cm"),
    "SKU-H": ("HUB-4USB", "Bộ chia USB 3.0, 4 cổng"),
}


def alternative_capacity(day, org):
    day = date.fromisoformat(str(day))
    return [
        dict(
            id=uuid5(org, f"decision-demo-v2:alternative-capacity:{day}:{offset}"),
            org_id=org,
            carrier_code="ANTIN-EXPRESS",
            capacity_date=day + timedelta(days=offset),
            max_dispatches=5,
            status="ACTIVE",
            created_at=datetime.combine(day - timedelta(days=1), time(17), VIETNAM),
            updated_at=datetime.combine(day - timedelta(days=1), time(17), VIETNAM),
        )
        for offset in range(7)
    ]


def profile(day):
    """Use the reviewed stochastic ledger generator, then label every reference.

    Simulation history includes Poisson arrivals, weekly/seasonal effects, intermittent
    and spiky demand, inbound delays and constrained carrier capacity. No column
    containing its latent parameters or outcome labels is published to forecasting.
    """
    day = date.fromisoformat(str(day))
    config = SimulationConfig(
        seed=20261026, reference_date=str(day - timedelta(days=1)), scale=1.0, days=240
    )
    rows = simulate(config)
    validation = validate(rows, config)
    if not validation["valid"]:
        raise ValueError("Base demo ledger invariants failed; no writes")
    warehouse = rows["warehouse"]["warehouses"][0]
    org, wh = warehouse["org_id"], warehouse["id"]

    def identity(key):
        return uuid5(org, "decision-demo-v1:" + key)

    def at(offset=0, hour=8):
        return datetime.combine(day + timedelta(days=offset), time(hour), VIETNAM)

    for domain in rows.values():
        for values in domain.values():
            for row in values:
                old = row.get("sku")
                if old in CATALOG:
                    row["sku"] = CATALOG[old][0]
                    for key in ("name", "product_name_snapshot"):
                        if key in row:
                            row[key] = CATALOG[old][1]
                if row.get("uom") == "EA":
                    row["uom"] = "cái"
                for key in ("carrier_name_snapshot",):
                    if key in row:
                        row[key] = {
                            "CARRIER-1": "Vận tải Minh Phát",
                            "CARRIER-2": "Giao nhận Thành Công",
                        }.get(row[key], row[key])
                if "recipient_name" in row:
                    row["recipient_name"] = "Nguyễn Minh Quân"
                if "recipient_name_snapshot" in row:
                    row["recipient_name_snapshot"] = "Nguyễn Minh Quân"
                for key in ("delivery_address_snapshot",):
                    if key in row:
                        row[key] = "Kho tiếp nhận, Khu công nghiệp Tân Bình, TP. Hồ Chí Minh"
                if "carrier_receiver_name" in row:
                    row["carrier_receiver_name"] = "Trần Văn Bình"
    warehouse.update(
        warehouse_code="BD-CENTRAL",
        name="Kho trung tâm Bình Dương",
        preparation_minutes=120,
        preparation_policy_revision=1,
        preparation_confirmed_at=at(-1, 17),
        preparation_confirmed_by="Phụ trách kho",
        updated_at=at(-1, 23),
    )
    for index, customer in enumerate(rows["sales"]["customers"]):
        customer.update(
            customer_code=f"KH-{index + 1:03}",
            name=[
                "Công ty Thiết bị An Phát",
                "Công ty Văn phòng Minh Tâm",
                "Công ty Thương mại Hoàng Nam",
                "Công ty Dịch vụ Nam Việt",
            ][index % 4]
            + f" — chi nhánh {index // 4 + 1}",
        )
    # The old history's manifests are source evidence: do not change payload fields
    # independently from their hash. Relabel its embedded SKU/UOM consistently.
    for event in rows["warehouse"].get("outbox_events", []):
        payload = event["payload"]
        for line in payload.get("lines", []):
            if line.get("sku") in CATALOG:
                line["sku"] = CATALOG[line["sku"]][0]
            if line.get("uom") == "EA":
                line["uom"] = "cái"
        event["payload_hash"] = canonical_hash(payload)
    # Relabel the historical fixture manifest consistently with its original
    # versioned format. New acceptance orders use today's native manifest API.
    from collections import defaultdict

    by_order = defaultdict(list)
    for line in rows["sales"]["sales_order_lines"]:
        by_order[line["order_id"]].append(line)
    reservations = {r["id"]: r for r in rows["warehouse"]["reservations"]}
    hashes = {}
    for dispatch in rows["warehouse"]["dispatches"]:
        order_id = reservations[dispatch["reservation_id"]]["sales_order_ref"]
        manifest = {
            "org_id": str(org),
            "order_ref": str(order_id),
            "lines": [
                {
                    "sales_order_line_ref": str(line["id"]),
                    "sku": line["sku"],
                    "quantity": str(line["ordered_quantity"]),
                    "uom": line["uom"],
                }
                for line in sorted(by_order[order_id], key=lambda r: r["line_no"])
            ],
        }
        dispatch["manifest_hash"] = canonical_hash(manifest)
        hashes[dispatch["id"]] = dispatch["manifest_hash"]
    for shipment in rows["logistics"]["shipments"]:
        shipment["manifest_hash"] = hashes[shipment["warehouse_dispatch_ref"]]
    labeled_validation = validate(rows, config)
    if not labeled_validation["valid"]:
        raise ValueError("Relabeled demo ledger invariants failed; no writes")
    products = {p["sku"]: p for p in rows["warehouse"]["products"]}
    balances = {b["product_id"]: b for b in rows["warehouse"]["inventory_balances"]}
    urgent = []
    for number, skus in ((1, ["KB-K120"]), (2, ["KB-K120", "MS-B100"])):
        oid = identity(f"urgent:{number}")
        order = dict(
            id=oid,
            org_id=org,
            order_number=f"SO-PHOIHOP-{number:03}",
            customer_id=rows["sales"]["customers"][number - 1]["id"],
            status="CONFIRMED",
            requested_handoff_at=at(1, 10),
            recipient_name="Nguyễn Minh Quân",
            recipient_phone="0900000000",
            delivery_address_snapshot="Kho tiếp nhận, Khu công nghiệp Tân Bình, TP. Hồ Chí Minh",
            currency="VND",
            fulfillment_warehouse_ref=wh,
            input_revision=1,
            version=1,
            created_by_subject="decision-demo-sales",
            confirmed_at=at(-1, 17),
            created_at=at(-1, 16),
            updated_at=at(-1, 17),
        )
        rows["sales"]["sales_orders"].append(order)
        rows["sales"]["sales_order_events"].append(
            dict(
                id=identity(f"urgent:{number}:confirmed"),
                org_id=org,
                order_id=oid,
                event_type="sales.order_confirmed",
                from_status="SUBMITTED",
                to_status="CONFIRMED",
                source_app="sales",
                actor_subject="decision-demo-sales",
                correlation_id=oid,
                details={},
                occurred_at=at(-1, 17),
                recorded_at=at(-1, 17),
            )
        )
        lines = []
        for index, sku in enumerate(skus, 1):
            product = products[sku]
            balance = balances[product["id"]]
            qty = (
                balance["on_hand_quantity"]
                - balance["reserved_quantity"]
                + _quantity(30 if number == 1 else 10)
            )
            line = dict(
                id=identity(f"urgent:{number}:line:{index}"),
                org_id=org,
                order_id=oid,
                line_no=index,
                product_ref=product["id"],
                sku=sku,
                product_name_snapshot=product["name"],
                uom=product["uom"],
                ordered_quantity=qty,
                unit_price=_quantity(150),
                created_at=at(-1, 16),
            )
            rows["sales"]["sales_order_lines"].append(line)
            lines.append({"sku": sku, "quantity": str(qty), "uom": product["uom"]})
        urgent.append(
            {
                "id": str(oid),
                "number": order["order_number"],
                "lines": lines,
                "deadline": at(1, 10).isoformat(),
            }
        )
    inbound_id = identity("late-inbound")
    rows["warehouse"]["inbound_plans"].append(
        dict(
            id=inbound_id,
            org_id=org,
            warehouse_id=wh,
            product_id=products["KB-K120"]["id"],
            planned_quantity=_quantity(40),
            expected_at=at(1, 14),
            status="APPROVED",
            created_at=at(-1, 16),
            updated_at=at(-1, 16),
        )
    )
    rows["logistics"]["handoff_capacity_calendar"].extend(alternative_capacity(day, org))
    summary = {
        "version": 2,
        "extensions": ["alternative_carrier_v1"],
        "alternative_carrier": "ANTIN-EXPRESS",
        "day": str(day),
        "org_id": str(org),
        "warehouse_id": str(wh),
        "history": {"days": 240, "seed": config.seed, "profile": "stochastic_ledger_v1"},
        "orders": urgent,
        "late_inbound_id": str(inbound_id),
        "base_validation": labeled_validation["checks"],
        "rows": {d: {t: len(rs) for t, rs in tables.items()} for d, tables in rows.items()},
        "boundary": (
            "Historical stochastic fixture; native operations on new acceptance orders; "
            "no real company facts"
        ),
    }
    return rows, summary


def seed(day):
    from sqlalchemy import text

    from scripts.database.config import database_url, validate_distinct_targets
    from scripts.database.engine import make_engine
    from scripts.database.local import BUSINESS, load_local
    from scripts.database.registry import metadata_for
    from scripts.database.seed import _validate_existing, insert_missing_rows

    load_local()
    validate_distinct_targets(BUSINESS)
    for domain in BUSINESS:
        target = database_url(domain)
        if (target.host, target.port, target.database, target.username) != (
            "127.0.0.1",
            55432,
            domain + "_db",
            domain + "_owner",
        ):
            raise ValueError(
                "Only explicitly guarded local demo owners on port 55432 are supported"
            )
    rows, summary = profile(day)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    ledger_file = DIRECTORY / "data-resources.json"
    if ledger_file.exists():
        ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
        if ledger["day"] != str(day) or ledger["org_id"] != summary["org_id"]:
            raise ValueError("An existing demo profile owns this directory; no replacement/reset")
    else:
        ledger = {**summary, "completed_domains": []}
        ledger_file.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    for domain in BUSINESS:
        if domain in ledger["completed_domains"]:
            continue  # Native demo changes are preserved; this command is not a reset.
        engine = make_engine(domain)
        try:
            with engine.begin() as conn:
                conn.execute(text("SELECT pg_advisory_xact_lock(477184113)"))
                _validate_existing(conn, metadata_for(domain), rows[domain])
                insert_missing_rows(conn, metadata_for(domain), rows[domain])
            ledger["completed_domains"].append(domain)
            ledger_file.write_text(
                json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        finally:
            engine.dispose()
    return ledger


def extend_capacity():
    """Append an explicitly owned alternative; never free existing bookings/capacity."""
    from uuid import UUID

    from sqlalchemy import text

    from scripts.database.config import database_url
    from scripts.database.engine import make_engine
    from scripts.database.local import load_local
    from scripts.database.registry import metadata_for
    from scripts.database.seed import _validate_existing, insert_missing_rows

    load_local()
    target = database_url("logistics")
    if (target.host, target.port, target.database, target.username) != (
        "127.0.0.1",
        55432,
        "logistics_db",
        "logistics_owner",
    ):
        raise ValueError("Alternative capacity only supports the owned local Logistics database")
    file = DIRECTORY / "data-resources.json"
    ledger = json.loads(file.read_text(encoding="utf-8"))
    if "logistics" not in ledger["completed_domains"]:
        raise ValueError("Complete the local seed before appending alternative capacity")
    records = alternative_capacity(ledger["day"], UUID(ledger["org_id"]))
    engine = make_engine("logistics", hide_parameters=True)
    try:
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(477184113)"))
            tables = {"handoff_capacity_calendar": records}
            _validate_existing(conn, metadata_for("logistics"), tables)
            insert_missing_rows(conn, metadata_for("logistics"), tables)
    finally:
        engine.dispose()
    extensions = ledger.setdefault("extensions", [])
    if "alternative_carrier_v1" not in extensions:
        extensions.append("alternative_carrier_v1")
        ledger["rows"]["logistics"]["handoff_capacity_calendar"] += len(records)
    ledger.update(version=2, alternative_carrier="ANTIN-EXPRESS")
    file.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"alternative_carrier": "ANTIN-EXPRESS", "days": 7, "slots_per_day": 5}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preview", "seed-local", "extend-local-capacity"))
    parser.add_argument("--day", default=str(datetime.now(VIETNAM).date()))
    args = parser.parse_args()
    summary = (
        extend_capacity()
        if args.operation == "extend-local-capacity"
        else seed(args.day)
        if args.operation == "seed-local"
        else profile(args.day)[1]
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
