# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Async Alembic environment sharing the backend's connection configuration."""

import asyncio
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.util import CommandError
from sqlalchemy import Connection

from flowbridge.db import create_engine
from flowbridge.models import Base
from flowbridge.settings import Settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # Supabase/extensions/other apps can own tables absent from our metadata.
    # Table drops must therefore be written explicitly in a reviewed migration.
    return not (type_ == "table" and reflected and compare_to is None)


def process_revision_directives(migration_context, revision, directives):
    if not target_metadata.tables:
        raise CommandError(
            "No ORM tables registered. Add models inheriting Base and import them in "
            "flowbridge/models/__init__.py before using --autogenerate."
        )


def connection_settings() -> Settings:
    # Resolve .env from the repository even when alembic -c is called elsewhere.
    settings = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env")
    if settings.database_url is None:
        raise CommandError(
            "Database is not configured. Fill FLOWBRIDGE_DATABASE_PASSWORD in .env "
            "with host/user settings, or set FLOWBRIDGE_DATABASE_URL."
        )
    return settings


def run_migrations_offline() -> None:
    settings = connection_settings()
    # Pass the URL directly: percent-encoded passwords must not pass ConfigParser interpolation.
    context.configure(
        url=settings.database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=False,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=False,
        include_object=include_object,
        compare_type=True,
        process_revision_directives=process_revision_directives,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_engine(connection_settings(), use_null_pool=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # Also support a caller-owned transaction, e.g. migration verification in tests.
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        do_run_migrations(supplied_connection)
    else:
        asyncio.run(run_async_migrations())
