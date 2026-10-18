# SPDX-License-Identifier: GPL-3.0-or-later
"""Refresh only the known synthetic seed; preserve timelines and ML snapshots.

An apply creates a local gzip backup before committing. Published Core versions
are retained: use the normal knowledge import/index commands after exporting MD.
"""

import argparse
import asyncio
import gzip
import hashlib
import json
import random
import runpy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from flowbridge.db import create_engine
from flowbridge.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "examples/rag/realistic_catalog.json"
ASOF = datetime.fromisoformat("2026-10-15T18:00:00+07:00")


def digest(value):
    raw = json.dumps(value, default=str, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def rows(connection, sql, **parameters):
    return [dict(row) for row in (await connection.execute(text(sql), parameters)).mappings()]


async def protected_digest(connection):
    result = {}
    for table in (
        "risk_predictions",
        "analysis_tasks",
        "goods_receipts",
        "purchase_order_schedules",
        "supplier_confirmations",
        "supplier_confirmation_items",
        "user_roles",
    ):
        result[table] = await rows(
            connection, f'SELECT to_jsonb(t) AS row FROM public."{table}" t ORDER BY 1::text'
        )
        result[table].sort(key=lambda row: json.dumps(row, default=str, sort_keys=True))
    return digest(result)


async def backup(connection, source):
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / ".cache/rag/data-refresh" / stamp
    folder.mkdir(parents=True, exist_ok=False)
    data = {"created_at": datetime.now(UTC).isoformat(), "tables": {}}
    for table in source["TABLES"]:
        data["tables"]["public." + table] = await rows(
            connection, f'SELECT to_jsonb(t) AS row FROM public."{table}" t'
        )
    for table in ("documents", "document_versions", "document_chunks"):
        data["tables"]["core." + table] = await rows(
            connection, f'SELECT to_jsonb(t) AS row FROM core."{table}" t'
        )
    with gzip.open(folder / "database-before.json.gz", "wt", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, default=str)
    # Keep every prior local document and binding, including the original provenance.
    import shutil

    shutil.copytree(ROOT / "examples/rag", folder / "local-before")
    (folder / "backup.sha256").write_text(
        hashlib.sha256((folder / "database-before.json.gz").read_bytes()).hexdigest() + "\n",
        encoding="utf-8",
    )
    return folder


async def refresh(connection, config, source):
    baseline = source["Demo"](1000, 20261015, ASOF).simulate()
    old_products = baseline.rows["products"]
    current = await rows(connection, "SELECT * FROM public.products ORDER BY product_code")
    by_code = {row["product_code"]: row for row in current}
    target = {product["code"]: product for product in config["products"]}
    if all(
        code in by_code and by_code[code]["product_name"] == p["name"] for code, p in target.items()
    ):
        return {"already_current": True}
    if len(current) != 6 or any(
        by_code.get(p["product_code"], {}).get("product_id") != p["product_id"]
        or by_code[p["product_code"]]["product_name"] != p["product_name"]
        for p in old_products
    ):
        raise RuntimeError("Unexpected catalog; refusing to overwrite unrecognized data")
    for table in ("suppliers", "products", "departments", "users"):
        key = source["COLUMNS"][table][0]
        expected = [row[key] for row in baseline.rows[table]]
        actual = await rows(
            connection,
            f'SELECT "{key}" FROM public."{table}" '
            f'WHERE "{key}"=ANY(CAST(:ids AS uuid[])) FOR UPDATE',
            ids=expected,
        )
        if len(actual) != len(expected):
            raise RuntimeError("Seed binding missing; transaction aborted")
    protected = await protected_digest(connection)
    folder = await backup(connection, source)
    old_ids = [product["product_id"] for product in old_products]
    for i, old in enumerate(old_products):
        product = target[old["product_code"]]
        if old["product_category"] != product["category"]:
            raise RuntimeError("A/B feature category must not change")
        params = {
            "product": old["product_id"],
            "name": product["name"],
            "unit": product["unit"],
            "description": product["spec"],
        }
        await connection.execute(
            text("""
            UPDATE public.products SET product_name=:name,unit=:unit,description=:description,
              updated_at=now() WHERE product_id=:product
        """),
            params,
        )
        factor = Decimal(product["base_price"]) / Decimal((i + 1) * 25000)
        # Restrict every financial correction to the exact generator-owned UUIDs.
        for table, column in (
            ("supplier_products", "current_unit_price"),
            ("purchase_request_items", "unit_price_snapshot"),
            ("analysis_supplier_candidates", "unit_price_snapshot"),
            ("proposal_items", "unit_price_snapshot"),
            ("purchase_order_items", "unit_price"),
        ):
            key = source["COLUMNS"][table][0]
            if table in ("supplier_products", "purchase_request_items", "purchase_order_items"):
                ids = [
                    row[key]
                    for row in baseline.rows[table]
                    if row["product_id"] == old["product_id"]
                ]
            else:
                request_ids = {
                    row["request_id"]
                    for row in baseline.rows["purchase_request_items"]
                    if row["product_id"] == old["product_id"]
                }
                ids = [row[key] for row in baseline.rows[table] if row["request_id"] in request_ids]
            quantity = {
                "purchase_request_items": "quantity",
                "purchase_order_items": "ordered_quantity",
            }.get(table)
            extra = f',line_total=round("{column}"*:factor,0)*{quantity}' if quantity else ""
            if table == "purchase_order_items":
                extra += ",unit=:unit"
            await connection.execute(
                text(
                    f'UPDATE public."{table}" SET "{column}"=round("{column}"*:factor,0)'
                    f'{extra} WHERE "{key}"=ANY(CAST(:ids AS uuid[]))'
                ),
                {"factor": factor, "ids": ids, "unit": product["unit"]},
            )
    po_ids = [row["purchase_order_id"] for row in baseline.rows["purchase_orders"]]
    await connection.execute(
        text("""
        UPDATE public.purchase_orders o SET total_amount=(SELECT sum(i.line_total)
          FROM public.purchase_order_items i WHERE i.purchase_order_id=o.purchase_order_id)
        WHERE o.purchase_order_id=ANY(CAST(:ids AS uuid[]))
    """),
        {"ids": po_ids},
    )
    for old, supplier in zip(baseline.rows["suppliers"], config["suppliers"], strict=True):
        await connection.execute(
            text("""
            UPDATE public.suppliers SET supplier_name=:name,address=:address,city=:city,
              updated_at=now() WHERE supplier_id=:id AND supplier_code=:code
        """),
            {**supplier, "id": old["supplier_id"]},
        )
    department_names = ["Hành chính — Nhân sự", "Công nghệ thông tin", "Vận hành kho", "Kế toán"]
    staff_names = [
        ["Lê Hoàng Anh", "Nguyễn Ngọc Mai", "Phạm Quốc Huy"],
        ["Trần Gia Bảo", "Võ Minh Thư", "Đặng Anh Khoa"],
        ["Nguyễn Thu Hà", "Lê Đức Toàn", "Phan Thảo Vy"],
        ["Trần Bảo Ngọc", "Huỳnh Quốc Nam", "Đỗ Ngọc Hân"],
    ]
    warehouse_names = ["Trần Minh An", "Nguyễn Thanh Tùng", "Lê Thị Ngọc", "Phạm Hoài Nam"]
    approver_names = ["Nguyễn Hồng Phúc", "Trần Quang Hưng", "Lê Minh Tuấn", "Võ Thu Trang"]
    for i, department in enumerate(baseline.rows["departments"]):
        await connection.execute(
            text("""
            UPDATE public.departments SET department_name=:name,updated_at=now()
            WHERE department_id=:id
        """),
            {"name": department_names[i], "id": department["department_id"]},
        )
    for user in baseline.rows["users"]:
        name = user["display_name"]
        if name.startswith("demo-warehouse-"):
            name = warehouse_names[int(name.rsplit("-", 1)[1])]
        elif name.startswith("demo-approver-"):
            name = approver_names[int(name.rsplit("-", 1)[1])]
        elif name.startswith("demo-staff-"):
            i, j = map(int, name.split("-")[-2:])
            name = staff_names[i][j]
        else:
            name = {
                "demo-admin": "Quản trị dữ liệu mẫu",
                "demo-ai": "Dịch vụ phân tích AI",
                "demo-po-service": "Dịch vụ phát hành PO",
            }.get(name, name)
        await connection.execute(
            text("UPDATE public.users SET display_name=:name WHERE user_id=:id"),
            {"name": name, "id": user["user_id"]},
        )
    # New catalog items have no invented historical PO or performance labels.
    now = datetime.now(UTC)
    rng = random.Random(20261017)
    for product in config["products"][6:]:
        pid = uuid5(NAMESPACE_URL, "flowbridge-realistic-catalog/" + product["code"])
        await connection.execute(
            text("""
            INSERT INTO public.products(product_id,product_code,product_name,product_category,
              unit,description,status,created_at,updated_at)
            VALUES(:id,:code,:name,:category,:unit,:spec,'active',:now,:now)
        """),
            {**product, "id": pid, "now": now},
        )
        for i, supplier in enumerate(baseline.rows["suppliers"]):
            price = round(product["base_price"] * (0.94 + 0.025 * i + rng.uniform(-0.025, 0.025)))
            await connection.execute(
                text("""
                INSERT INTO public.supplier_products(supplier_product_id,supplier_id,product_id,
                  current_unit_price,currency,min_order_qty,quoted_lead_days,is_available,updated_at)
                VALUES(:id,:supplier,:product,:price,'VND',1,:lead,true,:now)
            """),
                {
                    "id": uuid5(pid, str(supplier["supplier_id"])),
                    "supplier": supplier["supplier_id"],
                    "product": pid,
                    "price": price,
                    "lead": rng.randint(3, 12),
                    "now": now,
                },
            )
    if protected != await protected_digest(connection):
        raise RuntimeError("Protected timelines, roles or ML snapshots changed; rolling back")
    checks = await rows(
        connection,
        """
        SELECT count(*) FILTER (WHERE i.unit<>p.unit
          OR i.line_total<>i.unit_price*i.ordered_quantity)
          AS invalid_lines FROM public.purchase_order_items i
          JOIN public.products p USING(product_id)
          WHERE i.product_id=ANY(CAST(:ids AS uuid[]))
    """,
        ids=old_ids,
    )
    if checks[0]["invalid_lines"]:
        raise RuntimeError("Financial/unit verification failed; rolling back")
    result = {
        "backup": str(folder),
        "products": 24,
        "suppliers": 6,
        "protected_timelines_and_ml_sha256": protected,
        "historical_po_count": len(po_ids),
        "new_catalog_items_without_history": 18,
    }
    (folder / "applied-plan.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


async def run(apply):
    config = json.loads(CATALOG.read_text(encoding="utf-8"))
    source = runpy.run_path(str(ROOT / "scripts/supplier_demo_source.py"))
    engine = create_engine(Settings(_env_file=ROOT / ".env"), use_null_pool=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            await connection.execute(text("SET LOCAL lock_timeout='5s'"))
            await connection.execute(text("SET LOCAL statement_timeout='120s'"))
            if apply:
                locked = (
                    await connection.execute(
                        text(
                            "SELECT pg_try_advisory_xact_lock(hashtext('flowbridge.supplier-demo'))"
                        )
                    )
                ).scalar_one()
                if not locked:
                    raise RuntimeError("Another demo data writer is active")
                result = await refresh(connection, config, source)
                if (
                    await connection.execute(
                        text("SELECT to_regclass('core.documents') IS NOT NULL")
                    )
                ).scalar_one():
                    # Current list title follows the supplier. Immutable version
                    # title snapshots and original citations remain untouched.
                    await connection.execute(
                        text("""
                        UPDATE core.documents d SET title=s.supplier_name
                        FROM public.suppliers s
                        WHERE d.subject_type='supplier' AND d.document_kind='supplier_review'
                          AND d.subject_id=s.supplier_id::text
                          AND s.supplier_code=ANY(CAST(:codes AS text[]))
                    """),
                        {"codes": [supplier["code"] for supplier in config["suppliers"]]},
                    )
            else:
                result = {
                    "mode": "read-only",
                    "catalog_items": len(config["products"]),
                    "suppliers": await rows(
                        connection, "SELECT supplier_code FROM public.suppliers"
                    ),
                }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--apply", action="store_true")
    args = cli.parse_args()
    try:
        asyncio.run(run(args.apply))
    except Exception as error:
        print(f"Refresh stopped ({type(error).__name__}); transaction rolled back.")
        raise SystemExit(1) from None
