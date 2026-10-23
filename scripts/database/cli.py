"""Small explicit database administration CLI for local setup and CI."""

import argparse
import json
import sys
from pathlib import Path

from alembic import command
from alembic.util import CommandError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateSchema

from scripts.database.config import (
    database_url,
    layout,
    load_configuration,
    schema_for,
    validate_distinct_targets,
)
from scripts.database.engine import make_engine
from scripts.database.migration import alembic_config
from scripts.database.registry import DATABASES


def initialize_schemas(names=DATABASES):
    validate_distinct_targets(names)
    for name in names:
        schema = schema_for(name)
        engine = make_engine(name)
        try:
            with engine.begin() as conn:
                conn.execute(CreateSchema(schema, if_not_exists=True))
            print(f"{name}: schema {schema} ready")
        finally:
            engine.dispose()


def initialize_databases():
    if layout() == "schemas":
        initialize_schemas()
        return
    validate_distinct_targets()
    admin_url = database_url("admin")
    # Identifiers are quoted by the dialect, never interpolated as raw user SQL.
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            for name in DATABASES:
                url = database_url(name)
                if (url.host, url.port or 5432) != (
                    admin_url.host,
                    admin_url.port or 5432,
                ):
                    raise ValueError(
                        "init-databases supports a single PostgreSQL server; "
                        "create remote databases separately"
                    )
                exists = conn.scalar(
                    text("SELECT 1 FROM pg_database WHERE datname=:name"),
                    {"name": url.database},
                )
                if not exists:
                    identifier = conn.dialect.identifier_preparer.quote_identifier(url.database)
                    conn.exec_driver_sql(f"CREATE DATABASE {identifier}")
                print(f"{name}: {'exists' if exists else 'created'}")
    finally:
        engine.dispose()


def run_migrations(names, revision="head"):
    validate_distinct_targets(names)
    if layout() == "schemas":
        initialize_schemas(names)
    for name in names:
        command.upgrade(alembic_config(name), revision)
        print(f"{name}: migrated to {revision}")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="DX-OS: four domain histories in separate schemas or databases"
    )
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser(
        "init-databases",
        help="Create missing databases or schemas according to DXOS_LAYOUT",
    )
    sub.add_parser("init-schemas", help="Create the four namespaces in schema layout")
    for operation in ("upgrade", "status", "check", "seed", "verify", "sql"):
        child = sub.add_parser(operation)
        child.add_argument("--database", choices=(*DATABASES, "all"), default="all")
        if operation in ("upgrade", "sql"):
            child.add_argument("--revision", default="head")
        if operation == "sql":
            child.add_argument("--output", type=Path, help="Write UTF-8 SQL to a new file")
    bootstrap = sub.add_parser(
        "bootstrap",
        help="Initialize targets, run migrations, optionally load demo fixtures",
    )
    bootstrap.add_argument("--demo", action="store_true")
    realign = sub.add_parser(
        "realign-core",
        help="Back up and discard legacy Core metadata; never targets domain databases",
    )
    realign.add_argument("--confirm-database", required=True)
    args = parser.parse_args(argv)
    load_configuration()
    try:
        if args.operation == "realign-core":
            from scripts.database.realign import realign_core

            print(json.dumps(realign_core(args.confirm_database), ensure_ascii=True))
            return 0
        if args.operation in ("init-databases", "init-schemas", "bootstrap"):
            if args.operation == "init-schemas" and layout() != "schemas":
                raise ValueError("init-schemas requires DXOS_LAYOUT=schemas")
            initialize_databases()
            if args.operation == "bootstrap":
                run_migrations(DATABASES)
                if args.demo:
                    from scripts.database.seed import seed_databases

                    seed_databases(DATABASES)
                    from scripts.database.verify import verify_demo

                    print(json.dumps(verify_demo(), ensure_ascii=False, indent=2))
            return 0
        names = DATABASES if args.database == "all" else (args.database,)
        if args.operation == "sql" and len(names) != 1:
            raise ValueError(
                "SQL export targets one domain; specify --database sales/warehouse/logistics/core"
            )
        if args.operation == "upgrade":
            if len(names) > 1 and args.revision != "head":
                raise ValueError("Use --database core (or one target) with a specific revision")
            run_migrations(names, args.revision)
        elif args.operation == "seed":
            from scripts.database.seed import seed_databases

            seed_databases(names)
        elif args.operation == "verify":
            from scripts.database.verify import verify_demo

            if args.database != "all":
                raise ValueError(
                    "verify checks fixture references across all four domains; use --database all"
                )
            print(json.dumps(verify_demo(), ensure_ascii=False, indent=2))
        else:
            for name in names:
                config = alembic_config(name)
                print(f"-- {name}")
                if args.operation == "status":
                    command.current(config, verbose=True)
                elif args.operation == "check":
                    command.check(config)
                elif args.operation == "sql":
                    if args.output:
                        with args.output.open("x", encoding="utf-8") as output:
                            config.output_buffer = output
                            command.upgrade(config, args.revision, sql=True)
                        print(f"SQL written to {args.output.name}")
                    else:
                        command.upgrade(config, args.revision, sql=True)
        return 0
    except SQLAlchemyError as exc:
        # The driver exception/statement can contain credentials or business PII.
        code = getattr(getattr(exc, "orig", None), "sqlstate", None)
        print(
            f"Database operation failed ({type(exc).__name__}, SQLSTATE={code or 'unknown'}). "
            "Check local settings and server permissions.",
            file=sys.stderr,
        )
        return 1
    except (ValueError, RuntimeError, CommandError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except FileExistsError:
        print("SQL output file already exists; choose a new filename.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
