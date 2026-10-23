"""Configuration without emitting credentials to logs or command output."""

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url

from scripts.database.registry import DATABASES

_DEFAULT_SCHEMAS = {
    "sales": "sales",
    "warehouse": "warehouse",
    "logistics": "logistics",
    "core": "dxos_core",
}
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_RESERVED_SCHEMAS = {
    "public",
    "information_schema",
    "auth",
    "storage",
    "extensions",
    "graphql",
    "graphql_public",
    "realtime",
    "_realtime",
    "_analytics",
    "vault",
    "net",
    "pgsodium",
    "pgsodium_masks",
    "supabase_functions",
    "supabase_migrations",
}


def load_configuration() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)


def layout() -> str:
    """Keep the original four-database deployment as the default."""
    value = os.environ.get("DXOS_LAYOUT", "databases").strip().lower()
    if value not in {"databases", "schemas"}:
        raise ValueError("DXOS_LAYOUT must be 'databases' or 'schemas'")
    return value


def schema_for(database: str) -> str:
    """Return an allowlisted namespace, never a managed Supabase schema."""
    if database not in (*DATABASES, "admin"):
        raise ValueError("Unknown database target")
    if layout() == "databases" or database == "admin":
        return "public"
    value = os.environ.get(f"DXOS_{database.upper()}_SCHEMA", _DEFAULT_SCHEMAS[database]).strip()
    if not _IDENTIFIER.fullmatch(value) or value in _RESERVED_SCHEMAS or value.startswith("pg_"):
        raise ValueError(
            f"DXOS_{database.upper()}_SCHEMA must be a lowercase SQL identifier "
            "of at most 63 characters outside PostgreSQL/Supabase managed schemas"
        )
    return value


def database_url(database: str) -> URL:
    if database not in (*DATABASES, "admin"):
        raise ValueError("Unknown database target")
    value = os.environ.get(f"DXOS_{database.upper()}_URL", "").strip()
    if not value and layout() == "schemas":
        value = os.environ.get("DXOS_DATABASE_URL", "").strip()
    if not value:
        fallback = " or DXOS_DATABASE_URL" if layout() == "schemas" else ""
        raise ValueError(f"Set DXOS_{database.upper()}_URL{fallback} in .env or the environment")
    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Use postgresql+psycopg URLs (psycopg 3)")
    if not url.database:
        raise ValueError("A database name is required")
    return url


def _physical_identity(url: URL) -> tuple:
    """Recognize a Supabase project even behind a shared regional pooler."""
    host = (url.host or "").lower()
    if host.endswith(".pooler.supabase.com") and url.username:
        role_and_project = url.username.rsplit(".", 1)
        if len(role_and_project) == 2 and role_and_project[1]:
            return ("supabase", role_and_project[1], url.database)
    if host.startswith("db.") and host.endswith(".supabase.co"):
        return ("supabase", host[3 : -len(".supabase.co")], url.database)
    # A role change does not create a distinct physical PostgreSQL database.
    # libpq also allows a Unix socket/TCP host in URL query parameters.
    return (
        host or url.query.get("host"),
        int(url.port or url.query.get("port", 5432)),
        url.database,
    )


def validate_distinct_targets(names=DATABASES) -> None:
    # A one-target command must also catch aliases with other configured targets,
    # while allowing unconfigured targets to remain absent.
    names = tuple(names)
    if any(name not in DATABASES for name in names):
        raise ValueError("Unknown database target")
    shared = layout() == "schemas" and os.environ.get("DXOS_DATABASE_URL", "").strip()
    configured = [
        name
        for name in DATABASES
        if name in names or os.environ.get(f"DXOS_{name.upper()}_URL", "").strip() or shared
    ]
    targets = [database_url(name) for name in configured]
    physical = [_physical_identity(url) for url in targets]
    if layout() == "schemas":
        if len(set(physical)) > 1:
            raise ValueError("Schema layout requires all targets in the same physical database")
        identities = [
            (identity, schema_for(name))
            for name, identity in zip(configured, physical, strict=True)
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("Each target must use a different schema in the shared database")
    elif len(set(physical)) != len(physical):
        raise ValueError("Each target must point to a different physical database")
