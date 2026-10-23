"""Integration tests require an explicit disposable PostgreSQL server URL.

Every test run creates four randomly named databases, never uses .env target
databases, and removes only those exact names after closing connections.
"""

import os
from uuid import uuid4

import pytest
from alembic import command
from scripts.database.migration import alembic_config
from scripts.database.registry import DATABASES
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def databases():
    value = os.environ.get("DXOS_TEST_ADMIN_URL")
    if not value:
        pytest.skip("Set DXOS_TEST_ADMIN_URL for PostgreSQL integration tests")
    admin_url = make_url(value)
    assert admin_url.drivername == "postgresql+psycopg"
    prefix = f"dxos_it_{uuid4().hex[:10]}_"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    previous = {}
    created = []
    engines = {}
    try:
        # Integration tests select their layout explicitly; a developer's local
        # Supabase .env must never redirect this disposable legacy-layout run.
        for key in ("DXOS_LAYOUT", "DXOS_DATABASE_URL"):
            previous[key] = os.environ.get(key)
        for name in DATABASES:
            key = f"DXOS_{name.upper()}_URL"
            previous[key] = os.environ.get(key)
        os.environ["DXOS_LAYOUT"] = "databases"
        os.environ.pop("DXOS_DATABASE_URL", None)
        with admin.connect() as conn:
            for name in DATABASES:
                dbname = prefix + name
                # Generated allowlisted identifier, also dialect-quoted.
                quoted = conn.dialect.identifier_preparer.quote_identifier(dbname)
                conn.exec_driver_sql(f"CREATE DATABASE {quoted}")
                created.append(dbname)
                key = f"DXOS_{name.upper()}_URL"
                url = admin_url.set(database=dbname)
                os.environ[key] = url.render_as_string(hide_password=False)
                engines[name] = create_engine(url)
        # Set every target before Alembic's first .env load so missing values
        # cannot be filled from a developer's live configuration.
        for name in DATABASES:
            command.upgrade(alembic_config(name), "head")
        from scripts.database.seed import seed_databases

        seed_databases(DATABASES)
        yield engines
    finally:
        for engine in engines.values():
            engine.dispose()
        with admin.connect() as conn:
            for dbname in reversed(created):
                assert dbname.startswith(prefix)
                quoted = conn.dialect.identifier_preparer.quote_identifier(dbname)
                conn.exec_driver_sql(f"DROP DATABASE {quoted} WITH (FORCE)")
        admin.dispose()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
