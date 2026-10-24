"""Generate/validate local demo data with a recoverable per-database commit ledger.

Reset is opt-in and refuses any business row outside the recorded synthetic run.
Core registry and Hasura metadata are never reset or seeded here.
"""

import argparse
import json
import os
from contextlib import ExitStack
from dataclasses import asdict

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from scripts.database.config import database_url, validate_distinct_targets
from scripts.database.engine import make_engine
from scripts.database.fixtures.business import canonical_hash
from scripts.database.local import BUSINESS, RUNTIME, load_local
from scripts.database.registry import metadata_for
from scripts.database.seed import _validate_existing, insert_missing_rows
from scripts.database.simulation import SimulationConfig, simulate
from scripts.database.synthetic_validation import validate

LEDGER = RUNTIME / "synthetic-run.json"


def save_ledger(value):
    temporary = LEDGER.with_suffix(".pending")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(LEDGER)


def assert_targets():
    if os.environ.get("FLOWBRIDGE_DEMO_MODE") != "local-docker":
        raise ValueError("Synthetic writes require explicit local-docker demo mode")
    validate_distinct_targets(BUSINESS)
    for d in BUSINESS:
        url = database_url(d)
        if (url.host, url.database, url.username) != ("127.0.0.1", f"{d}_db", f"{d}_owner"):
            raise ValueError(
                "Synthetic writes require loopback, exact demo database and owner allowlist"
            )


def read_rows(connections, *, org_id=None, exclude_workflow=False):
    return {
        d: {
            t.name: [
                dict(r)
                for r in conn.execute(
                    sa.select(t).where(t.c.org_id == org_id) if org_id is not None else sa.select(t)
                ).mappings()
            ]
            for t in metadata_for(d).sorted_tables
            if not (exclude_workflow and t.name.startswith("wf_e2e_"))
        }
        for d, conn in connections.items()
    }


def assert_owned(conn, domain, candidates):
    """Every existing row must belong to an unchanged, recorded run candidate."""
    metadata = metadata_for(domain)
    actual = read_rows({domain: conn})[domain]
    for table, records in actual.items():
        allowed = {r["id"] for candidate in candidates for r in candidate[domain].get(table, [])}
        unknown = [r["id"] for r in records if r["id"] not in allowed]
        if unknown:
            raise ValueError(
                f"Refusing demo reset/write: {domain}.{table} contains unowned row {unknown[0]}"
            )
    for candidate in candidates:
        _validate_existing(conn, metadata, candidate[domain])


def snapshot_hash(tables):
    return canonical_hash(
        {
            table: sorted(json.dumps(r, sort_keys=True, default=str) for r in records)
            for table, records in tables.items()
        }
    )


def generate(config, reset=False, dry_run=False):
    rows = simulate(config)
    result = validate(rows, config)
    if not result["valid"]:
        return result
    if dry_run:
        result["storage"] = "DRY_RUN; no database writes"
        return result
    assert_targets()
    previous = json.loads(LEDGER.read_text()) if LEDGER.exists() else None
    current = asdict(config)
    old_config = previous.get("config") if previous else None
    if old_config and old_config != current and not reset:
        raise ValueError("A different demo exists; use --reset-demo explicitly")
    if previous and previous.get("state") == "IN_PROGRESS" and old_config != current:
        raise ValueError(
            "Resume the interrupted run with its original options before changing configuration"
        )
    candidates = [rows]
    previous_config = (
        old_config
        if old_config != current
        else previous.get("previous_config")
        if previous
        else None
    )
    if previous_config:
        candidates.append(simulate(SimulationConfig(**previous_config)))
    with ExitStack() as stack:
        connections = {}
        for d in BUSINESS:
            engine = make_engine(d, hide_parameters=True, connect_args={"connect_timeout": 10})
            stack.callback(engine.dispose)
            conn = stack.enter_context(engine.connect())
            conn.execute(sa.text("SET lock_timeout='10s'"))
            conn.execute(sa.text("SET statement_timeout='120s'"))
            # Hold independent session locks across preflight and all commits.
            conn.execute(sa.text("SELECT pg_advisory_lock(477184113)"))
            conn.commit()
            stack.callback(
                lambda c=conn: c.execute(sa.text("SELECT pg_advisory_unlock(477184113)"))
            )
            connections[d] = conn
        for d, conn in connections.items():
            assert_owned(conn, d, candidates)
            expected_hash = (previous or {}).get("snapshot_hashes", {}).get(d)
            if previous and previous["state"] == "COMPLETE" and expected_hash:
                actual_hash = snapshot_hash(read_rows({d: conn})[d])
                if actual_hash != expected_hash:
                    raise ValueError(f"Refusing demo reset/write: {d} changed since recorded seed")
            conn.commit()
        ledger = {
            "config": current,
            "previous_config": previous_config,
            "state": "IN_PROGRESS",
            "committed": [],
        }
        save_ledger(ledger)
        inserted = {}
        for d, conn in connections.items():
            metadata = metadata_for(d)
            with conn.begin():
                if reset or previous_config:
                    # Per-domain delete+insert is atomic. A crash leaves each domain
                    # wholly old or wholly new; the persisted ledger permits resume.
                    for table in reversed(metadata.sorted_tables):
                        conn.execute(table.delete())
                inserted[d] = insert_missing_rows(conn, metadata, rows[d])
            ledger["committed"].append(d)
            save_ledger(ledger)
        persisted = read_rows(connections)
        result = validate(persisted, config)
        if not result["valid"]:
            raise ValueError(
                "Persisted business validation failed: " + json.dumps(result["errors"])
            )
        ledger.update(
            state="COMPLETE",
            previous_config=None,
            snapshot_hashes={d: snapshot_hash(tables) for d, tables in persisted.items()},
        )
        save_ledger(ledger)
        result["inserted"] = inserted
        result["storage"] = "Three independently committed databases; replay ledger COMPLETE"
        return result


def inspect_dataset():
    assert_targets()
    if not LEDGER.exists():
        raise ValueError("No recorded synthetic run; generate first")
    ledger = json.loads(LEDGER.read_text())
    if ledger["state"] != "COMPLETE":
        raise ValueError(
            "Interrupted seed: rerun generate with the recorded configuration to resume"
        )
    config = SimulationConfig(**ledger["config"])
    with ExitStack() as stack:
        connections = {}
        for d in BUSINESS:
            engine = make_engine(d, hide_parameters=True)
            stack.callback(engine.dispose)
            connections[d] = stack.enter_context(engine.connect())
        org = simulate(config)["sales"]["customers"][0]["org_id"]
        result = validate(read_rows(connections, org_id=org, exclude_workflow=True), config)
        result["scope"] = {"synthetic_org_id": str(org), "other_organizations": "excluded"}
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("generate", "validate", "stats"))
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument("--reference-date", default="2026-10-20")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset-demo", action="store_true")
    args = parser.parse_args()
    if args.operation != "generate" and (args.dry_run or args.reset_demo):
        parser.error("--dry-run/--reset-demo require generate")
    if not args.dry_run:
        load_local()
    try:
        result = (
            generate(
                SimulationConfig(args.seed, args.reference_date, args.scale, args.days),
                args.reset_demo,
                args.dry_run,
            )
            if args.operation == "generate"
            else inspect_dataset()
        )
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 1
    except SQLAlchemyError as exc:
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "sqlstate": getattr(getattr(exc, "orig", None), "sqlstate", None),
                    "recovery": "Run ledger retained; fix the error then retry the same command",
                }
            )
        )
        return 1
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
