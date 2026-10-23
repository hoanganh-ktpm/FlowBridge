"""Explicit, backed-up replacement of the legacy Core metadata schema only."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy.schema import CreateTable

from scripts.database.config import database_url, layout, validate_distinct_targets
from scripts.database.engine import make_engine
from scripts.database.migration import alembic_config


def require_core_target(confirmation: str) -> None:
    if confirmation != "dxos_core_db" or layout() != "databases":
        raise ValueError("Explicit dxos_core_db confirmation and database layout are required")
    validate_distinct_targets(("core",))
    if database_url("core").database != confirmation:
        raise ValueError("Core URL does not match the confirmed database")


def realign_core(confirmation: str, backup_directory: Path = Path("backups")) -> dict:
    require_core_target(confirmation)
    config = alembic_config("core")
    revision = ScriptDirectory.from_config(config).get_revision("core_0004").module
    expected = set(revision.LEGACY_TABLES)
    backup_directory = backup_directory.resolve()
    workspace = Path.cwd().resolve()
    if not backup_directory.is_relative_to(workspace / "backups"):
        raise ValueError("Backups must stay inside this checkout's ignored backups directory")
    engine = make_engine("core", poolclass=sa.pool.NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(sa.text("SET LOCAL statement_timeout = '60s'"))
            actual_database = connection.scalar(sa.text("SELECT current_database()"))
            if actual_database != confirmation:
                raise ValueError("Connected database does not match confirmation")
            connection.execute(
                sa.text("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
            )
            current = connection.scalar(sa.text("SELECT version_num FROM public.alembic_version"))
            if current == "core_0004":
                return {"database": actual_database, "head": current, "changed": False}
            if current != "core_0003":
                raise ValueError("Only the reviewed core_0003 handoff may be realigned")
            actual = set(sa.inspect(connection).get_table_names(schema="public"))
            if actual != expected | {"alembic_version"}:
                raise ValueError("Unexpected Core tables; review before discarding any metadata")
            preparer = connection.dialect.identifier_preparer
            targets = ", ".join(
                "public." + preparer.quote_identifier(name) for name in sorted(expected)
            )
            connection.execute(sa.text("LOCK TABLE " + targets + " IN ACCESS EXCLUSIVE MODE"))
            metadata = sa.MetaData()
            metadata.reflect(connection, schema="public", only=sorted(expected))
            rows = {}
            for table in metadata.sorted_tables:
                records = connection.execute(sa.select(table).limit(10001)).mappings().all()
                if len(records) > 10000:
                    raise ValueError("Core backup exceeds the reviewed bounded reset profile")
                rows[table.name] = [dict(record) for record in records]
            snapshot = {
                "database": actual_database,
                "revision": current,
                "captured_at": datetime.now(UTC).isoformat(),
                "format": "logical-metadata-json; restore requires reviewed type-aware tooling",
                "ddl": [
                    str(CreateTable(table).compile(dialect=connection.dialect))
                    for table in metadata.sorted_tables
                ],
                "rows": rows,
            }
            backup_directory.mkdir(parents=True, exist_ok=True)
            backup = backup_directory / f"core-before-realignment-{uuid4().hex}.json"
            with backup.open("x", encoding="utf-8") as output:
                json.dump(snapshot, output, ensure_ascii=False, default=str, indent=2)
                output.flush()
                os.fsync(output.fileno())
            config.attributes["connection"] = connection
            config.attributes["allow_core_data_reset"] = True
            command.upgrade(config, "core_0004")
            command.check(config)
            result = {
                "database": actual_database,
                "head": "core_0004",
                "changed": True,
                "removed_rows": sum(map(len, rows.values())),
                "backup": str(backup.relative_to(workspace)),
            }
        return result
    finally:
        engine.dispose()
