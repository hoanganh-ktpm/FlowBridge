"""Create a credential-redacted PostgreSQL engine without connecting or migrating."""

import asyncio
import ssl

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


async def wait_for_database(engine, *, attempts=5, delay=0.5, timeout=15):
    """Bound startup retries and never expose a driver exception/connection URL."""
    try:
        async with asyncio.timeout(timeout):
            for attempt in range(attempts):
                try:
                    async with engine.connect() as connection:
                        await connection.execute(text("SELECT 1"))
                    return
                except (SQLAlchemyError, OSError):
                    if attempt == attempts - 1:
                        break
                    await asyncio.sleep(delay)
    except TimeoutError:
        pass
    raise RuntimeError(
        "Core database readiness failed; check local service and credentials"
    ) from None


def core_engine(database_url: str, *, pool_size: int = 0):
    if type(pool_size) is not int or not 0 <= pool_size <= 10:
        raise ValueError("Core connection pool size must be an integer from 0 to 10")
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
        **(
            {
                "pool_size": pool_size,
                "max_overflow": 0,
                "pool_pre_ping": True,
                "pool_timeout": 15,
                "pool_recycle": 300,
            }
            if pool_size
            else {"poolclass": NullPool}
        ),
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
