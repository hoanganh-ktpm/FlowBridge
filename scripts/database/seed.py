"""Explicit, deterministic demo loading; never a production data migration."""

from collections import Counter

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
        for record in records:
            existing = (
                conn.execute(
                    select(*(table.c[key] for key in record)).where(table.c.id == record["id"])
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None and any(
                existing[key] != value for key, value in record.items()
            ):
                raise RuntimeError(
                    f"Demo fixture conflict in {table_name}: an existing fixture row was changed. "
                    "Seed does not overwrite user data."
                )


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
                _validate_existing(conn, metadata, all_rows[name])
                for table in metadata.sorted_tables:
                    for record in all_rows[name].get(table.name, []):
                        exists = conn.scalar(select(table.c.id).where(table.c.id == record["id"]))
                        if exists is None:
                            conn.execute(table.insert().values(**record))
                            counter[table.name] += 1
            summary[name] = sum(counter.values())
            print(f"{name}: {summary[name]} demo rows inserted (existing identical rows preserved)")
        finally:
            engine.dispose()
    return summary
