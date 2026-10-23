"""PostgreSQL-only integration: isolated temporary DBs; no production reset."""

import os
from uuid import uuid4

import pytest
from alembic import command
from scripts.database.migration import alembic_config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def isolated_postgres():
    value = os.environ.get("DXOS_TEST_ADMIN_URL")
    if not value:
        pytest.skip("DXOS_TEST_ADMIN_URL is required; never fall back to application URLs")
    admin_url = make_url(value)
    if admin_url.drivername != "postgresql+psycopg":
        pytest.fail("Use the explicit psycopg test admin connection")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    prefix = "flowbridge_it_" + uuid4().hex[:12] + "_"
    created = []
    urls = {}
    try:
        with admin.connect() as connection:
            for name in ("core", "stock", "consumer"):
                database = prefix + name
                quoted = connection.dialect.identifier_preparer.quote_identifier(database)
                connection.exec_driver_sql("CREATE DATABASE " + quoted)
                created.append(database)
                urls[name] = admin_url.set(database=database)
        engine = create_engine(urls["core"], hide_parameters=True)
        try:
            with engine.begin() as connection:
                config = alembic_config("core")
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        finally:
            engine.dispose()
        engine = create_engine(urls["stock"], hide_parameters=True)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE public.stock_available (sku text PRIMARY KEY, "
                    "available_quantity integer NOT NULL, unit_cost numeric NOT NULL)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO public.stock_available VALUES ('SYNTHETIC-SKU', 7, 999)"
                )
        finally:
            engine.dispose()
        engine = create_engine(urls["consumer"], hide_parameters=True)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE public.order_refs (order_id text PRIMARY KEY)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO public.order_refs VALUES ('SYNTHETIC-ORDER')"
                )
        finally:
            engine.dispose()
        yield urls
    finally:
        with admin.connect() as connection:
            for database in reversed(created):
                assert database.startswith(prefix) and database in created
                quoted = connection.dialect.identifier_preparer.quote_identifier(database)
                connection.exec_driver_sql("DROP DATABASE " + quoted + " WITH (FORCE)")
        admin.dispose()
