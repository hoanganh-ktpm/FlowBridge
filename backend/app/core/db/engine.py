"""Create a credential-redacted PostgreSQL engine without connecting or migrating."""

import ssl

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


def core_engine(database_url: str):
    url = make_url(database_url)
    if url.drivername not in {"postgresql", "postgresql+asyncpg", "postgresql+psycopg"}:
        raise ValueError("Core requires a PostgreSQL URL")
    if url.port == 6543 and (url.host or "").endswith(".pooler.supabase.com"):
        raise ValueError("Session locks require a direct or session-pooler connection")
    mode = url.query.get("sslmode", "verify-full")
    if mode not in {"require", "verify-ca", "verify-full", "disable"}:
        raise ValueError("Unsupported PostgreSQL TLS mode")
    if mode == "disable" and url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Unencrypted PostgreSQL is restricted to loopback")
    context = ssl.create_default_context()
    if mode == "require":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif mode == "verify-ca":
        context.check_hostname = False
    return create_async_engine(
        url.set(drivername="postgresql+asyncpg", query={}),
        poolclass=NullPool,
        echo=False,
        hide_parameters=True,
        connect_args={
            "ssl": False if mode == "disable" else context,
            "timeout": 10,
            "command_timeout": 25,
            "prepared_statement_cache_size": 0,
            "server_settings": {"application_name": "flowbridge_core"},
        },
    )
