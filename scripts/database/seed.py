"""Explicit, deterministic demo loading; never a production data migration."""

from collections import Counter, defaultdict

from sqlalchemy import select, text

from scripts.database.config import validate_distinct_targets
from scripts.database.engine import make_engine
from scripts.database.registry import metadata_for


def demo_rows():
    from scripts.database.fixtures.business import business_seed_rows
    from scripts.database.fixtures.core import core_seed_rows

    rows = business_seed_rows()
    rows["core"] = core_seed_rows()
    return rows


def _validate_existing(conn, metadata, rows):
    for table_name, records in rows.items():
        table = metadata.tables[f"public.{table_name}"]
        if not records:
            continue
        # Batch reads instead of one round trip per fixture record.
        existing_rows = {}
        for offset in range(0, len(records), 1000):
            ids = [record["id"] for record in records[offset : offset + 1000]]
            existing_rows.update(
                {
                    record["id"]: record
                    for record in conn.execute(select(table).where(table.c.id.in_(ids))).mappings()
                }
            )
        for record in records:
            existing = existing_rows.get(record["id"])
            if existing is not None and any(
                existing[key] != value for key, value in record.items()
            ):
                raise RuntimeError(
                    f"Demo fixture conflict in {table_name}: an existing fixture row was changed. "
                    "Seed does not overwrite user data."
                )


def insert_missing_rows(conn, metadata, rows):
    """Shared batch loader for the small handoff fixture and timeline generator."""
    _validate_existing(conn, metadata, rows)
    count = 0
    for table in metadata.sorted_tables:
        records = rows.get(table.name, [])
        if not records:
            continue
        existing = set(conn.scalars(select(table.c.id)))
        missing = [record for record in records if record["id"] not in existing]
        groups = defaultdict(list)
        for record in missing:
            groups[tuple(sorted(record))].append(record)
        for group in groups.values():
            for offset in range(0, len(group), 500):
                conn.execute(table.insert(), group[offset : offset + 500])
        count += len(missing)
    return count


def seed_databases(names):
    validate_distinct_targets(names)
    all_rows = demo_rows()
    # Preflight every selected database before writing any of them. Each database
    # still commits separately; this is deliberately not advertised as cross-DB ACID.
    for name in names:
        engine = make_engine(name)
        try:
            with engine.connect() as conn:
                _validate_existing(conn, metadata_for(name), all_rows[name])
        finally:
            engine.dispose()
    summary = {}
    for name in names:
        metadata = metadata_for(name)
        engine = make_engine(name)
        counter = Counter()
        try:
            with engine.begin() as conn:
                # Serializes this seed loader with itself inside the local DB.
                conn.execute(text("SELECT pg_advisory_xact_lock(477184112)"))
                counter["total"] = insert_missing_rows(conn, metadata, all_rows[name])
            summary[name] = sum(counter.values())
            print(f"{name}: {summary[name]} demo rows inserted (existing identical rows preserved)")
        finally:
            engine.dispose()
    return summary


def verify_demo_rows(names):
    """Require every deterministic fixture row to exist unchanged."""
    validate_distinct_targets(names)
    all_rows = demo_rows()
    summary = {}
    for name in names:
        metadata = metadata_for(name)
        engine = make_engine(name)
        try:
            with engine.connect() as conn:
                _validate_existing(conn, metadata, all_rows[name])
                expected = 0
                present = 0
                for table_name, records in all_rows[name].items():
                    identifiers = [record["id"] for record in records]
                    expected += len(identifiers)
                    if identifiers:
                        table = metadata.tables[f"public.{table_name}"]
                        present += len(
                            conn.scalars(
                                select(table.c.id).where(table.c.id.in_(identifiers))
                            ).all()
                        )
                if present != expected:
                    raise RuntimeError(
                        f"Demo fixture verification failed for {name}: {present}/{expected} rows"
                    )
                summary[name] = present
        finally:
            engine.dispose()
    return summary


def remove_demo_rows(names):
    """Delete only unchanged deterministic fixture IDs, in reverse FK order."""
    validate_distinct_targets(names)
    all_rows = demo_rows()
    for name in names:
        metadata = metadata_for(name)
        engine = make_engine(name)
        try:
            with engine.begin() as conn:
                conn.execute(text("SELECT pg_advisory_xact_lock(477184112)"))
                _validate_existing(conn, metadata, all_rows[name])
                deleted = 0
                for table in reversed(metadata.sorted_tables):
                    identifiers = [record["id"] for record in all_rows[name].get(table.name, [])]
                    if identifiers:
                        deleted += conn.execute(
                            table.delete().where(table.c.id.in_(identifiers))
                        ).rowcount
            print(f"{name}: {deleted} unchanged demo rows removed")
        finally:
            engine.dispose()
