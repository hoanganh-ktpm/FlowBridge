# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply/check the additive Core SQL when three Core tables were created outside Alembic."""

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import text

from flowbridge.db import create_engine
from flowbridge.settings import Settings

EXPECTED = {
    "documents": {
        "document_id",
        "document_kind",
        "subject_type",
        "subject_id",
        "title",
        "scope_type",
        "department_id",
        "owner_subject",
        "created_at",
        "deleted_at",
    },
    "document_versions": {
        "version_id",
        "document_id",
        "version_no",
        "title_snapshot",
        "content_md",
        "checksum_sha256",
        "status",
        "metadata",
        "created_by_subject",
        "created_at",
        "published_at",
        "known_from",
        "withdrawn_at",
    },
    "document_chunks": {
        "chunk_id",
        "version_id",
        "chunk_no",
        "content",
        "heading_path",
        "source_start_byte",
        "source_end_byte",
        "source_checksum",
        "chunk_checksum",
        "embedding",
        "embedding_profile",
        "index_generation",
        "search_vector",
        "created_at",
    },
}


async def check(connection):
    for table, expected in EXPECTED.items():
        rows = (
            (
                await connection.execute(
                    text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='core' AND table_name=:table
        """),
                    {"table": table},
                )
            )
            .scalars()
            .all()
        )
        if set(rows) != expected:
            raise RuntimeError(f"Unexpected columns in core.{table}; review the existing schema")
    properties = (
        await connection.execute(
            text("""
        SELECT count(*)=3 AND bool_and(c.relrowsecurity) FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='core' AND c.relname IN ('documents','document_versions','document_chunks')
    """)
        )
    ).scalar_one()
    embedding = (
        await connection.execute(
            text("""
        SELECT format_type(a.atttypid,a.atttypmod) FROM pg_attribute a
        WHERE a.attrelid='core.document_chunks'::regclass AND a.attname='embedding'
    """)
        )
    ).scalar_one()
    triggers = (
        await connection.execute(
            text("""
        SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgenabled<>'D'
          AND (tgrelid='core.document_versions'::regclass
               AND tgname='trg_guard_published_document_versions'
               OR tgrelid='core.document_chunks'::regclass
               AND tgname='trg_guard_document_chunk_source')
    """)
        )
    ).scalar_one()
    if not properties or not embedding.endswith("vector(1024)") or triggers != 2:
        raise RuntimeError("Core RLS, vector dimension or source guards are missing")
    # Read-only checks never assert full authorization or retrieval quality.
    print("CHECK OK: 3 Core tables / 37 columns / vector(1024) / RLS / 2 source guards")


async def run(apply):
    settings = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env")
    if settings.database_url is None:
        print("Database is not configured. Fill FLOWBRIDGE_DATABASE_* locally.")
        return 2
    engine = create_engine(settings, use_null_pool=True)
    try:
        async with engine.begin() as connection:
            if apply:
                sql = (Path(__file__).resolve().parents[1] / "alembic/sql/core_0003.sql").read_text(
                    encoding="utf-8"
                )
                # Preflight existing tables before applying anything. Incompatible schemas abort.
                for table, expected in EXPECTED.items():
                    rows = (
                        (
                            await connection.execute(
                                text("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema='core' AND table_name=:name
                    """),
                                {"name": table},
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if rows and set(rows) != expected:
                        raise RuntimeError(
                            "Existing Core columns differ; no changes were committed"
                        )
                await connection.run_sync(
                    lambda sync: sync.connection.dbapi_connection.run_async(
                        lambda driver: driver.execute(sql)
                    )
                )
            await check(connection)
        return 0
    except Exception as exc:
        print(
            f"Core setup/check failed ({type(exc).__name__}). Transaction rolled back; "
            "check compatible columns, pgvector schema/version and database permissions."
        )
        return 1
    finally:
        await engine.dispose()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    group = cli.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true")
    group.add_argument("--check", action="store_true")
    args = cli.parse_args()
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
