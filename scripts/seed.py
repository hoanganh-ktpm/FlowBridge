# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Run the supplied synthetic generator using the project's async connection and migrations."""

import argparse
import asyncio
import json
import runpy
from datetime import date, datetime, time
from pathlib import Path

from flowbridge.db import create_engine
from flowbridge.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SCHEMA = {
    "tables": 27,
    "columns": 293,
    "primary_keys": 27,
    "foreign_keys": 56,
    "checks": 83,
    "ordinary_uniques": 40,
    "partial_uniques": 3,
    "rls_tables": 27,
    "client_table_grants": 0,
    "policies": 0,
    "user_triggers": 0,
}


async def seed_database(source: dict, demo) -> dict:
    settings = Settings(_env_file=ROOT / ".env")
    if settings.database_url is None:
        raise RuntimeError("Database is not configured in .env.")
    engine = create_engine(settings, use_null_pool=True)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            await connection.exec_driver_sql("SET LOCAL statement_timeout = '120s'")
            await connection.exec_driver_sql("SET LOCAL TIME ZONE 'Asia/Ho_Chi_Minh'")
            locked = (
                await connection.exec_driver_sql(
                    "SELECT pg_try_advisory_xact_lock(hashtext('flowbridge.supplier-demo'))"
                )
            ).scalar_one()
            if not locked:
                raise RuntimeError("Another seed is running. Try again after it completes.")
            actual = dict((await connection.exec_driver_sql(source["VERIFY_SQL"])).mappings().one())
            for key, expected in EXPECTED_SCHEMA.items():
                if actual[key] != expected:
                    raise RuntimeError(
                        f"Schema mismatch for {key}; run alembic upgrade head first."
                    )
            if actual["application_rows"]:
                raise RuntimeError(
                    "Application tables already contain data. Seed stopped without changing data."
                )
            raw = await connection.get_raw_connection()
            for statement in demo.seed_statements():
                await raw.driver_connection.execute(statement)
            counts = {}
            for table in source["TABLES"]:
                count = (
                    await connection.exec_driver_sql(f'SELECT count(*) FROM public."{table}"')
                ).scalar_one()
                if count != len(demo.rows[table]):
                    raise RuntimeError(f"Seed row-count mismatch for {table}.")
                counts[table] = count
            training_count = (
                await connection.exec_driver_sql(
                    "SELECT count(*) FROM public.sa_training_rows($1)", (demo.asof,)
                )
            ).scalar_one()
            expected_labels = sum(
                demo.completed(sample["order"], demo.asof) for sample in demo.samples
            )
            if training_count != expected_labels:
                raise RuntimeError("Training-row helper mismatch; seed transaction rolled back.")
            actual = dict((await connection.exec_driver_sql(source["VERIFY_SQL"])).mappings().one())
            actual.update(row_counts=counts, training_rows=training_count)
        return actual
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--as-of", default="2026-10-15")
    parser.add_argument("--out", type=Path, default=ROOT / "demo_output")
    parser.add_argument(
        "--dry-run", action="store_true", help="Generate CSVs/report without DB access"
    )
    args = parser.parse_args()
    if not 50 <= args.orders <= 20000:
        parser.error("--orders must be between 50 and 20000")
    source = runpy.run_path(str(Path(__file__).with_name("supplier_demo_source.py")))
    try:
        asof = datetime.combine(date.fromisoformat(args.as_of), time(18), source["TZ"])
    except ValueError:
        parser.error("--as-of must be YYYY-MM-DD")
    demo = source["Demo"](args.orders, args.seed, asof).simulate()
    # Validate data generation/exports and directory permissions before touching the DB.
    report = demo.exports(args.out)
    if args.dry_run:
        print(f"Dry run: {sum(report['row_counts'].values())} rows; no database connection.")
        return 0
    try:
        verification = asyncio.run(seed_database(source, demo))
    except Exception as error:
        if isinstance(error, RuntimeError):
            print(str(error))
        else:
            print(f"Seed failed ({type(error).__name__}); transaction rolled back.")
        return 1
    (args.out / "database_verification.json").write_text(
        json.dumps(verification, default=str, indent=2), encoding="utf-8"
    )
    print(f"Seed committed: {verification['application_rows']} rows in 27 tables.")
    print(f"Training helper: {verification['training_rows']} labeled rows.")
    print(f"CSV and verification reports: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
