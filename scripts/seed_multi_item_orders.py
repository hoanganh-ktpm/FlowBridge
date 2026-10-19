# SPDX-License-Identifier: GPL-3.0-or-later
"""Add coherent multi-item demo baskets; default dry-run, --apply commits atomically.

One PO belongs to one supplier. A mixed-supplier PR becomes several POs.
Selected snapshots are explicitly snapshot_only, as in the baseline demo seed;
they are not model scores or real workflow approvals. No notifications are queued.
"""

import argparse
import asyncio
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from flowbridge.db import create_engine, create_session_factory
from flowbridge.settings import Settings
from flowbridge.supplier_assistant.database_service import BUSINESS_TIMEZONE
from flowbridge.supplier_assistant.prediction.features import FeatureBuilder
from flowbridge.supplier_assistant.prediction.repository import PredictionRepository
from flowbridge.supplier_assistant.repository import SupplierRepository, table

ROOT = Path(__file__).resolve().parents[1]
BATCH = "flowbridge-multi-item-demo-v1"
TABLES = (
    "purchase_requests",
    "purchase_request_items",
    "analysis_tasks",
    "risk_predictions",
    "risk_analysis_reports",
    "analysis_supplier_candidates",
    "proposals",
    "proposal_items",
    "approval_history",
    "purchase_orders",
    "purchase_order_items",
    "purchase_order_schedules",
    "goods_receipts",
)


def identifier(name):
    return uuid5(NAMESPACE_URL, BATCH + "/" + name)


def stamp(day, hour=9):
    return datetime.combine(day, time(hour), BUSINESS_TIMEZONE)


def scenarios(anchor):
    # Fixed cases exercise full-receipt, split-receipt, partial and future delivery.
    return [
        {
            "code": "001",
            "title": "Trang bị bàn làm việc và vật tư văn phòng",
            "issue": anchor - timedelta(days=19),
            "due": anchor - timedelta(days=8),
            "lines": [
                ("DEMO-S01", "DEMO-P05", 30, [(anchor - timedelta(days=11), 30)]),
                ("DEMO-S01", "DEMO-P02", 30, [(anchor - timedelta(days=5), 30)]),
                (
                    "DEMO-S01",
                    "DEMO-P01",
                    40,
                    [(anchor - timedelta(days=10), 30), (anchor - timedelta(days=4), 10)],
                ),
            ],
        },
        {
            "code": "002",
            "title": "Vật tư đóng gói cho kho, còn thiếu một phần băng keo",
            "issue": anchor - timedelta(days=12),
            "due": anchor - timedelta(days=4),
            "lines": [
                ("DEMO-S04", "DEMO-P03", 120, [(anchor - timedelta(days=6), 80)]),
                ("DEMO-S04", "DEMO-P06", 24, [(anchor - timedelta(days=4), 24)]),
            ],
        },
        {
            "code": "003",
            "title": "Bổ sung văn phòng phẩm và thiết bị, chia hai nhà cung cấp",
            "issue": anchor - timedelta(days=1),
            "due": anchor + timedelta(days=9),
            "lines": [
                ("DEMO-S02", "DEMO-P01", 100, []),
                ("DEMO-S02", "DEMO-P04", 200, []),
                ("DEMO-S03", "DEMO-P05", 25, []),
                ("DEMO-S03", "DEMO-P02", 25, []),
            ],
        },
    ]


async def master_data(repo):
    products = {p["product_code"]: p for p in await repo.rows("products")}
    suppliers = {s["supplier_code"]: s for s in await repo.rows("suppliers")}
    offers = {(o["supplier_id"], o["product_id"]): o for o in await repo.rows("supplier_products")}
    users = {u["user_id"]: u for u in await repo.rows("users")}
    roles = {r["role_id"]: r for r in await repo.rows("roles") if r["status"] == "active"}
    grants = await repo.rows("user_roles", table("user_roles").c.revoked_at.is_(None))

    def actor(role, department=None):
        for grant in sorted(grants, key=lambda g: str(g["user_id"])):
            user, spec = users.get(grant["user_id"]), roles.get(grant["role_id"])
            if not user or not spec or user["status"] != "active" or spec["role_code"] != role:
                continue
            account = "service" if role in {"AI_SERVICE", "PO_SERVICE"} else "human"
            scope = "department" if role in {"APPROVER", "WAREHOUSE"} else "global"
            if (
                user["account_type"] != account
                or spec["allowed_account_type"] != account
                or grant["scope_type"] != scope
                or spec["scope_type"] != scope
            ):
                continue
            if scope == "department" and grant["department_id"] != department:
                continue
            if role == "EMPLOYEE" and user["department_id"] != department:
                continue
            return user["user_id"]
        raise RuntimeError("No active actor with the required database grant: " + role)

    departments = await repo.rows("departments", table("departments").c.status == "active")
    for department in departments:
        try:
            actors = {
                role: actor(role, department["department_id"])
                for role in (
                    "EMPLOYEE",
                    "APPROVER",
                    "WAREHOUSE",
                    "AI_SERVICE",
                    "PO_SERVICE",
                )
            }
            return products, suppliers, offers, department, actors
        except RuntimeError:
            continue
    raise RuntimeError("No department has the complete active demo actor set")


async def build_plan(repo, anchor, recorded_at):
    if anchor > recorded_at.astimezone(BUSINESS_TIMEZONE).date():
        raise RuntimeError("Demo anchor cannot be in the future")
    products, suppliers, offers, department, actors = await master_data(repo)
    rows = {name: [] for name in TABLES}
    preview, order_no = [], 0
    builder = FeatureBuilder()
    predictions = PredictionRepository(repo)

    def add(name, row):
        rows[name].append(row)

    for case in scenarios(anchor):
        number = case["code"]
        request_id, task_id, report_id, proposal_id = [
            identifier(f"{number}/{k}")
            for k in (
                "request",
                "task",
                "report",
                "proposal",
            )
        ]
        created = stamp(case["issue"] - timedelta(days=2))
        issue = stamp(case["issue"], 10)
        request_code = "DEMO-PR-MULTI-" + number
        add(
            "purchase_requests",
            {
                "request_id": request_id,
                "request_code": request_code,
                "department_id": department["department_id"],
                "requested_by": actors["EMPLOYEE"],
                "source": "api",
                "status": "ordered",
                "input_revision": 1,
                "submitted_at": created,
                "notes": f"{BATCH}: {case['title']}; synthetic test fixture",
                "created_at": created,
                "updated_at": issue,
            },
        )
        add(
            "analysis_tasks",
            {
                "task_id": task_id,
                "request_id": request_id,
                "po_item_id": None,
                "task_type": "initial_analysis",
                "input_revision": 1,
                "feature_asof": created,
                "input_snapshot": {
                    "demo": True,
                    "seed_batch": BATCH,
                    "request_id": str(request_id),
                },
                "idempotency_key": f"{BATCH}/{number}/task",
                "status": "succeeded",
                "started_at": created,
                "completed_at": created + timedelta(seconds=4),
                "error_code": None,
                "error_message": None,
                "created_at": created,
            },
        )
        add(
            "risk_analysis_reports",
            {
                "report_id": report_id,
                "task_id": task_id,
                "request_id": request_id,
                "primary_prediction_id": None,
                "risk_level": None,
                "summary": "Synthetic historical basket; fixed catalog selection, no model run.",
                "report_data": {
                    "demo": True,
                    "seed_batch": BATCH,
                    "model_executed": False,
                    "price_source": "current_catalog_assumption",
                },
                "created_at": created + timedelta(seconds=5),
            },
        )
        add(
            "proposals",
            {
                "proposal_id": proposal_id,
                "proposal_code": "DEMO-PROP-MULTI-" + number,
                "request_id": request_id,
                "version_no": 1,
                "input_revision": 1,
                "supersedes_proposal_id": None,
                "is_current": True,
                "analysis_report_id": report_id,
                "generation_task_id": task_id,
                "selection_policy_version": BATCH,
                "workflow_instance_id": None,
                "reason": "Synthetic historical import; fixed basket, no model-generated decision.",
                "status": "approved",
                "created_by": actors["AI_SERVICE"],
                "submitted_at": created + timedelta(seconds=8),
                "created_at": created + timedelta(seconds=8),
            },
        )
        add(
            "approval_history",
            {
                "approval_id": identifier(number + "/approval"),
                "proposal_id": proposal_id,
                "workflow_task_id": None,
                "approver_id": actors["APPROVER"],
                "decision": "approved",
                "reason": "Synthetic fixture approval, not a real workflow action.",
                "decided_at": issue - timedelta(hours=1),
            },
        )
        groups = defaultdict(list)
        for index, (supplier_code, product_code, quantity, receipts) in enumerate(case["lines"], 1):
            product, supplier = products[product_code], suppliers[supplier_code]
            offer = offers[(supplier["supplier_id"], product["product_id"])]
            qty, price = Decimal(quantity), offer["current_unit_price"]
            if (
                product["status"] != "active"
                or supplier["status"] != "active"
                or not offer["is_available"]
                or offer["currency"] != "VND"
                or qty < (offer["min_order_qty"] or Decimal(0))
            ):
                raise RuntimeError("Fixture line does not match an eligible current catalog offer")
            request_item, prediction_id, proposal_item = [
                identifier(f"{number}/{index}/{k}")
                for k in (
                    "request-item",
                    "snapshot",
                    "proposal-item",
                )
            ]
            add(
                "purchase_request_items",
                {
                    "request_item_id": request_item,
                    "request_id": request_id,
                    "line_number": index,
                    "product_id": product["product_id"],
                    "preferred_supplier_id": None,
                    "quantity": qty,
                    "requested_delivery_date": case["due"],
                    "unit_price_snapshot": price,
                    "currency": "VND",
                    "line_total": price * qty,
                    "notes": BATCH,
                },
            )
            facts = await predictions.facts(product, [supplier["supplier_id"]], created, 180)
            snapshot = builder.build(
                product=product,
                supplier_id=supplier["supplier_id"],
                quantity=qty,
                unit=product["unit"],
                deadline=case["due"],
                asof=created,
                facts=facts,
            )
            add(
                "risk_predictions",
                {
                    "prediction_id": prediction_id,
                    "task_id": task_id,
                    "request_item_id": request_item,
                    "request_id": request_id,
                    "input_revision": 1,
                    "source_type": "historical_import",
                    "prediction_stage": "PR_CREATED",
                    "po_item_id": None,
                    "idempotency_key": f"{BATCH}/{number}/{index}/snapshot",
                    "supplier_id": supplier["supplier_id"],
                    "product_id": product["product_id"],
                    "quantity": qty,
                    "requested_delivery_date": case["due"],
                    "feature_asof": created,
                    "requested_lead_days": (case["due"] - created.date()).days,
                    "feature_schema_version": "tabicl_supplier_fixed_v1",
                    "preprocessing_version": "not-run:synthetic-raw-v1",
                    "model_name": None,
                    "model_version": None,
                    "context_dataset_version": None,
                    "context_metadata": None,
                    "risk_score": None,
                    "risk_threshold": None,
                    "risk_level": None,
                    "feature_snapshot": {
                        **snapshot.model_dump(mode="json"),
                        "demo": True,
                        "seed_batch": BATCH,
                        "model_executed": False,
                    },
                    "status": "snapshot_only",
                    "error_code": None,
                    "error_message": None,
                    "predicted_at": created + timedelta(seconds=3),
                },
            )
            add(
                "analysis_supplier_candidates",
                {
                    "candidate_id": identifier(f"{number}/{index}/candidate"),
                    "report_id": report_id,
                    "task_id": task_id,
                    "request_id": request_id,
                    "request_item_id": request_item,
                    "supplier_id": supplier["supplier_id"],
                    "prediction_id": prediction_id,
                    "unit_price_snapshot": price,
                    "currency": "VND",
                    "rank": 1,
                    "is_eligible": True,
                    "reason": "Synthetic fixed basket; no inferred ranking.",
                    "evidence_refs": [],
                },
            )
            add(
                "proposal_items",
                {
                    "proposal_item_id": proposal_item,
                    "proposal_id": proposal_id,
                    "request_id": request_id,
                    "request_item_id": request_item,
                    "selected_supplier_id": supplier["supplier_id"],
                    "prediction_id": prediction_id,
                    "quantity": qty,
                    "unit_price_snapshot": price,
                    "currency": "VND",
                    "proposed_delivery_date": case["due"],
                    "reason": "Synthetic fixed catalog allocation; historical-import fixture.",
                },
            )
            groups[supplier_code].append(
                (index, product, qty, price, request_item, proposal_item, receipts)
            )
        for supplier_code, lines in groups.items():
            order_no += 1
            order_code = f"DEMO-PO-MULTI-{order_no:03}"
            order_id = identifier(order_code)
            fully_received = all(sum(q for _, q in line[-1]) == line[2] for line in lines)
            received_any = any(line[-1] for line in lines)
            add(
                "purchase_orders",
                {
                    "purchase_order_id": order_id,
                    "po_number": order_code,
                    "request_id": request_id,
                    "proposal_id": proposal_id,
                    "supplier_id": suppliers[supplier_code]["supplier_id"],
                    "order_date": issue,
                    "status": "delivered"
                    if fully_received
                    else "partially_received"
                    if received_any
                    else "issued",
                    "currency": "VND",
                    "total_amount": sum((line[2] * line[3] for line in lines), Decimal(0)),
                    "pdf_file_id": None,
                    "created_by": actors["PO_SERVICE"],
                    "created_at": issue,
                    "updated_at": recorded_at if received_any else issue,
                },
            )
            display = []
            for line_no, (
                _,
                product,
                qty,
                price,
                request_item,
                proposal_item,
                receipts,
            ) in enumerate(lines, 1):
                item_id, schedule_id = (
                    identifier(f"{order_code}/{line_no}/item"),
                    identifier(f"{order_code}/{line_no}/schedule"),
                )
                received = sum(q for _, q in receipts)
                status = (
                    "completed" if received == qty else "partially_received" if received else "open"
                )
                add(
                    "purchase_order_items",
                    {
                        "po_item_id": item_id,
                        "purchase_order_id": order_id,
                        "proposal_item_id": proposal_item,
                        "proposal_id": proposal_id,
                        "request_id": request_id,
                        "request_item_id": request_item,
                        "line_number": line_no,
                        "product_id": product["product_id"],
                        "product_category_snapshot": product["product_category"],
                        "ordered_quantity": qty,
                        "unit": product["unit"],
                        "unit_price": price,
                        "line_total": price * qty,
                        "item_status": status,
                    },
                )
                add(
                    "purchase_order_schedules",
                    {
                        "schedule_id": schedule_id,
                        "po_item_id": item_id,
                        "schedule_number": 1,
                        "scheduled_quantity": qty,
                        "requested_delivery_date": case["due"],
                        "promised_delivery_date": None,
                        "status": status,
                        "created_at": issue,
                    },
                )
                for receipt_no, (actual_date, amount) in enumerate(receipts, 1):
                    add(
                        "goods_receipts",
                        {
                            "receipt_id": identifier(
                                f"{order_code}/{line_no}/receipt/{receipt_no}"
                            ),
                            "schedule_id": schedule_id,
                            "received_quantity": Decimal(amount),
                            "actual_delivery_date": actual_date,
                            "received_at": recorded_at,
                            "recorded_by": actors["WAREHOUSE"],
                            "notes": BATCH
                            + "; synthetic historical receipt imported at recorded_at",
                        },
                    )
                display.append(
                    {
                        "product": product["product_name"],
                        "code": product["product_code"],
                        "quantity": str(qty),
                        "unit": product["unit"],
                        "received": str(received),
                    }
                )
            preview.append(
                {
                    "request_code": request_code,
                    "po_number": order_code,
                    "supplier": suppliers[supplier_code]["supplier_name"],
                    "items": display,
                }
            )
    return rows, preview


def validate_plan(rows):
    orders = {r["purchase_order_id"]: r for r in rows["purchase_orders"]}
    proposals = {r["proposal_item_id"]: r for r in rows["proposal_items"]}
    for order_id, order in orders.items():
        lines = [i for i in rows["purchase_order_items"] if i["purchase_order_id"] == order_id]
        assert len({i["product_id"] for i in lines}) >= 2
        assert sum((i["line_total"] for i in lines), Decimal(0)) == order["total_amount"]
        for line in lines:
            selected = proposals[line["proposal_item_id"]]
            assert selected["selected_supplier_id"] == order["supplier_id"]
            assert selected["request_item_id"] == line["request_item_id"]
            assert line["line_total"] == line["ordered_quantity"] * line["unit_price"]
            plans = [
                s for s in rows["purchase_order_schedules"] if s["po_item_id"] == line["po_item_id"]
            ]
            assert sum(s["scheduled_quantity"] for s in plans) == line["ordered_quantity"]
            for plan in plans:
                receipts = [
                    r for r in rows["goods_receipts"] if r["schedule_id"] == plan["schedule_id"]
                ]
                assert sum(r["received_quantity"] for r in receipts) <= plan["scheduled_quantity"]
                assert all(
                    r["actual_delivery_date"] >= order["order_date"].date() for r in receipts
                )
                assert all(
                    r["actual_delivery_date"]
                    <= r["received_at"].astimezone(BUSINESS_TIMEZONE).date()
                    for r in receipts
                )


def fingerprint(rows):
    return hashlib.sha256(
        json.dumps(rows, default=str, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def verify_existing(rows, existing):
    if any(len(existing[n]) != len(rows[n]) for n in TABLES):
        raise RuntimeError("Partial seed already exists; refusing to overwrite")
    for name in TABLES:
        pk = list(table(name).primary_key.columns)[0].name
        expected = {r[pk]: r for r in rows[name]}
        for row in existing[name]:
            for key, value in expected[row[pk]].items():
                if key not in {"feature_snapshot", "input_snapshot", "received_at", "updated_at"}:
                    if row[key] != value:
                        raise RuntimeError("Installed fixture has changed; refusing to overwrite")


async def install(apply=False, anchor=date(2026, 10, 20)):
    engine = create_engine(Settings(_env_file=ROOT / ".env"), use_null_pool=True)
    try:
        sessions = create_session_factory(engine)
        async with sessions() as session, session.begin():
            await session.execute(
                sa.text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                    + ("" if apply else ", READ ONLY")
                )
            )
            await session.execute(sa.text("SET LOCAL statement_timeout='120s'"))
            await session.execute(sa.text("SET LOCAL lock_timeout='5s'"))
            if apply:
                lock = await session.scalar(
                    sa.text(
                        "SELECT pg_try_advisory_xact_lock(hashtext('flowbridge.supplier-demo'))"
                    )
                )
                if not lock:
                    raise RuntimeError("Another demo writer is active")
            repo = SupplierRepository(session)
            rows, preview = await build_plan(repo, anchor, datetime.now(UTC))
            validate_plan(rows)
            existing = {}
            for name in TABLES:
                pk = list(table(name).primary_key.columns)[0]
                existing[name] = await repo.rows(name, pk.in_([r[pk.name] for r in rows[name]]))
            found = sum(map(len, existing.values()))
            if found:
                verify_existing(rows, existing)
                return {
                    "mode": "already_applied",
                    "row_counts": {n: len(r) for n, r in rows.items()},
                    "orders": preview,
                }
            result = {
                "mode": "apply" if apply else "dry_run",
                "row_counts": {n: len(r) for n, r in rows.items()},
                "orders": preview,
            }
            if not apply:
                return result
            folder = (
                ROOT / ".cache/rag/multi-item-seed" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            )
            folder.mkdir(parents=True, exist_ok=False)
            backup = {n: await repo.rows(n) for n in TABLES}
            with gzip.open(folder / "before.json.gz", "wt", encoding="utf-8") as stream:
                json.dump(backup, stream, default=str, ensure_ascii=False)
            (folder / "before.sha256").write_text(
                hashlib.sha256((folder / "before.json.gz").read_bytes()).hexdigest(),
                encoding="utf-8",
            )
            for name in TABLES:
                for row in rows[name]:
                    await repo.insert(name, row)
            await session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
            for name in TABLES:
                pk = list(table(name).primary_key.columns)[0]
                ids = [r[pk.name] for r in rows[name]]
                old_rows = await repo.rows(name, pk.not_in(ids))
                if fingerprint(old_rows) != fingerprint(backup[name]):
                    raise RuntimeError("Pre-existing rows changed; transaction rolls back")
                if await repo.count(name, pk.in_(ids)) != len(rows[name]):
                    raise RuntimeError("Inserted row-count mismatch; transaction rolls back")
            # Re-read inserted aggregates: no subtotal or row-count may drift.
            for order in rows["purchase_orders"]:
                saved = await repo.aggregate(
                    "purchase_orders", await repo.get("purchase_orders", order["purchase_order_id"])
                )
                if sum(Decimal(i["line_total"]) for i in saved["items"]) != Decimal(
                    saved["total_amount"]
                ):
                    raise RuntimeError("Persisted total mismatch; transaction rolls back")
            manifest = {
                n: [str(r[list(table(n).primary_key.columns)[0].name]) for r in data]
                for n, data in rows.items()
            }
            (folder / "manifest.json").write_text(
                json.dumps(
                    {"batch": BATCH, "rows": manifest, "preview": preview},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            result["backup_directory"] = str(folder)
        return result
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(
            json.dumps(asyncio.run(install(args.apply)), default=str, ensure_ascii=False, indent=2)
        )
    except Exception as error:
        print("Seed stopped; transaction rolled back: " + type(error).__name__)
        raise SystemExit(1) from None
