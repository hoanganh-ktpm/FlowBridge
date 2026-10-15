# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Migration/configuration checks use temporary SQLite only; no Supabase I/O."""

import io
import os
import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.util import CommandError
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from flowbridge.db import create_engine
from flowbridge.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def migration_config(monkeypatch, tmp_path):
    class TestBase(DeclarativeBase):
        pass

    monkeypatch.setattr("flowbridge.models.Base", TestBase)
    for name in os.environ:
        if name.startswith("FLOWBRIDGE_"):
            monkeypatch.delenv(name)
    migration_dir = tmp_path / "alembic"
    (migration_dir / "versions").mkdir(parents=True)
    for filename in ("env.py", "script.py.mako"):
        shutil.copyfile(ROOT / "alembic" / filename, migration_dir / filename)
    configuration = Config(str(ROOT / "alembic.ini"), output_buffer=io.StringIO())
    configuration.set_main_option("script_location", str(migration_dir))
    configuration.attributes["test_metadata"] = TestBase.metadata
    return configuration


@pytest.mark.asyncio
@pytest.mark.parametrize("force_null", [False, True])
async def test_null_pool_for_supabase_and_migrations(force_null):
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@localhost/postgres",
        database_pool_mode="queue" if force_null else "null",
    )
    engine = create_engine(settings, use_null_pool=force_null)
    try:
        assert isinstance(engine.pool, NullPool)
    finally:
        await engine.dispose()


def test_offline_sql_from_revision_handles_percent_encoded_password(migration_config, monkeypatch):
    monkeypatch.setenv("FLOWBRIDGE_DATABASE_HOST", "localhost")
    monkeypatch.setenv("FLOWBRIDGE_DATABASE_USER", "user")
    monkeypatch.setenv("FLOWBRIDGE_DATABASE_PASSWORD", "special@/%# password")
    revision = command.revision(migration_config, message="test schema", rev_id="0001")
    revision_path = Path(revision.path)
    source = revision_path.read_text(encoding="utf-8").replace(
        "    pass",
        '    op.create_table("migration_test", sa.Column("id", sa.Integer(), primary_key=True))',
        1,
    )
    revision_path.write_text(source, encoding="utf-8")
    command.upgrade(migration_config, "head", sql=True)
    sql = migration_config.output_buffer.getvalue()
    assert "CREATE TABLE migration_test" in sql
    assert "alembic_version" in sql
    assert "0001" in sql
    assert "special" not in sql


def test_missing_credentials_fail_before_connecting(migration_config):
    with pytest.raises(CommandError, match="Database is not configured"):
        command.upgrade(migration_config, "head", sql=True)


def test_autogenerate_requires_registered_models(migration_config):
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            migration_config.attributes["connection"] = connection
            with pytest.raises(CommandError, match="No ORM tables registered"):
                command.revision(migration_config, message="empty", autogenerate=True)
    finally:
        engine.dispose()


def test_autogenerate_creates_owned_table_and_keeps_unmanaged_table(migration_config):
    engine = sa.create_engine("sqlite://")
    metadata = migration_config.attributes["test_metadata"]
    table = sa.Table("owned_test", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE unrelated_table (id INTEGER PRIMARY KEY)"))
            migration_config.attributes["connection"] = connection
            revision = command.revision(
                migration_config, message="owned table", autogenerate=True, rev_id="0002"
            )
            migration_source = Path(revision.path).read_text(encoding="utf-8")
            assert "owned_test" in migration_source
            assert "unrelated_table" not in migration_source
            command.upgrade(migration_config, "head")
            inspector = sa.inspect(connection)
            assert inspector.has_table("owned_test")
            assert inspector.has_table("unrelated_table")
            command.downgrade(migration_config, "base")
            inspector = sa.inspect(connection)
            assert not inspector.has_table("owned_test")
            assert inspector.has_table("unrelated_table")
    finally:
        metadata.remove(table)
        engine.dispose()
