"""Explicit local Docker configuration. Never loads the legacy .env or Supabase DSNs."""

import argparse
import json
import os
import secrets
import socket
import subprocess
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from sqlalchemy.engine import URL

from scripts.database.registry import REPOSITORY_ROOT

RUNTIME = REPOSITORY_ROOT / ".tmp/local-postgres"
COMPOSE = REPOSITORY_ROOT / "infra/postgres/docker-compose.local.yml"
BUSINESS = ("sales", "warehouse", "logistics")
PRODUCT_VIEWS = {
    "sales": (
        "v_confirmed_demand_daily",
        "v_open_orders",
        "v_planning_demand_daily",
        "v_order_register",
        "v_order_register_lines",
    ),
    "warehouse": (
        "v_stock_availability",
        "v_inbound_schedule",
        "v_order_commitments",
        "v_planning_catalog",
        "v_receipts_daily",
        "v_handoff_preparation_policy",
        "v_location_register",
        "v_product_register",
    ),
    "logistics": ("v_handoff_capacity", "v_accepted_shipments_daily", "v_handoff_schedule"),
}


def configure(port=55432):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    target = RUNTIME / "compose.env"
    values = (
        dict(dotenv_values(target))
        if target.exists()
        else {
            key: secrets.token_hex(24)
            for key in (
                "POSTGRES_PASSWORD",
                "SALES_PASSWORD",
                "WAREHOUSE_PASSWORD",
                "LOGISTICS_PASSWORD",
                "CORE_PASSWORD",
                "METADATA_PASSWORD",
                "READER_PASSWORD",
                "HASURA_ADMIN_SECRET",
                "JWT_KEY",
            )
        }
    )
    port = int(values.get("POSTGRES_PORT", port))
    values["POSTGRES_PORT"] = str(port)
    values["HASURA_JWT_CONFIG"] = json.dumps(
        {
            "type": "HS256",
            "key": values["JWT_KEY"],
            "issuer": "flowbridge-core",
            "audience": "flowbridge-data",
        },
        separators=(",", ":"),
    )
    target.write_text("".join(f"{k}='{v}'\n" for k, v in values.items()), encoding="utf-8")
    tls = RUNTIME / "tls"
    tls.mkdir(exist_ok=True)
    if not (tls / "server.key").exists():
        openssl = (
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/openssl.exe"
        )
        subprocess.run(
            [
                str(openssl),
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "365",
                "-subj",
                "/CN=postgres",
                "-addext",
                "subjectAltName=DNS:postgres,DNS:localhost,IP:127.0.0.1",
                "-keyout",
                str(tls / "server.key"),
                "-out",
                str(tls / "server.crt"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    host = {"DXOS_LAYOUT": "databases", "FLOWBRIDGE_DEMO_MODE": "local-docker"}
    for domain in (*BUSINESS, "core", "admin"):
        db = {"core": "dxos_core_db", "admin": "postgres"}.get(domain, f"{domain}_db")
        role = "flowbridge_admin" if domain == "admin" else f"{domain}_owner"
        password = values[
            "POSTGRES_PASSWORD" if domain == "admin" else f"{domain.upper()}_PASSWORD"
        ]
        host[f"DXOS_{domain.upper()}_URL"] = URL.create(
            "postgresql+psycopg",
            username=role,
            password=password,
            host="127.0.0.1",
            port=port,
            database=db,
            query={"sslmode": "require"},
        ).render_as_string(hide_password=False)
    host["FLOWBRIDGE_CORE_DATABASE_URL"] = host["DXOS_CORE_URL"]
    host["FLOWBRIDGE_HASURA_ENDPOINT"] = "http://127.0.0.1:18082"
    host["FLOWBRIDGE_HASURA_ADMIN_SECRET"] = values["HASURA_ADMIN_SECRET"]
    host["FLOWBRIDGE_CORE_HASURA_JWT_SIGNING_KEY"] = values["JWT_KEY"]
    host["FLOWBRIDGE_CORE_HASURA_JWT_ISSUER"] = "flowbridge-core"
    host["FLOWBRIDGE_CORE_HASURA_JWT_AUDIENCE"] = "flowbridge-data"
    host["FLOWBRIDGE_CORE_VERIFIED_HASURA_AUTH_PROFILE"] = "true"
    host["FLOWBRIDGE_CORE_SOURCE_SCOPES"] = json.dumps(
        {
            f"{d}-reader": [{"schema": "public", "name": name} for name in views]
            for d, views in PRODUCT_VIEWS.items()
        }
    )
    from app.core.applications.manifest import source_name
    from scripts.database.products import view_keys

    host["FLOWBRIDGE_CORE_SOURCE_VIEW_KEYS"] = json.dumps(view_keys())
    host["FLOWBRIDGE_WORKFLOW_ENABLED"] = "false"
    host["FLOWBRIDGE_HASURA_ALLOWED_SOURCES"] = json.dumps([source_name(d, "pg") for d in BUSINESS])
    host["FLOWBRIDGE_HASURA_ALLOWED_DATABASE_HOSTS"] = '["postgres","127.0.0.1"]'
    host["FLOWBRIDGE_HASURA_SECRET_BINDINGS"] = json.dumps(
        {f"{d}-reader": f"{d.upper()}_READ_DATABASE_URL" for d in BUSINESS}
    )
    for d in BUSINESS:
        host[f"{d.upper()}_READ_DATABASE_URL"] = URL.create(
            "postgresql",
            username=f"{d}_reader",
            password=values["READER_PASSWORD"],
            host="127.0.0.1",
            port=port,
            database=f"{d}_db",
            query={"sslmode": "require"},
        ).render_as_string(hide_password=False)
    (RUNTIME / "host.env").write_text(
        "".join(f"{k}='{v}'\n" for k, v in host.items()), encoding="utf-8"
    )
    print("Local secrets/configuration saved under ignored .tmp/local-postgres")


def load_local():
    if not (RUNTIME / "host.env").is_file():
        raise ValueError("Run python -m scripts.database.local configure first")
    # Explicitly overrides legacy environment; no fallback to old hosted credentials.
    load_dotenv(RUNTIME / "host.env", override=True)


def compose(*args):
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(RUNTIME / "compose.env"),
            "-f",
            str(COMPOSE),
            *args,
        ],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "configure",
            "up",
            "bootstrap",
            "upgrade",
            "check",
            "status",
            "stop",
            "restart",
            "hasura",
            "readers",
            "products",
            "verify",
        ),
    )
    parser.add_argument("--port", type=int, default=55432)
    args = parser.parse_args()
    if args.operation == "configure":
        configure(args.port)
        return
    load_local()
    if args.operation == "up":
        port = int(dotenv_values(RUNTIME / "compose.env")["POSTGRES_PORT"])
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError(f"Port {port} is occupied; use restart for the existing stack")
        compose("up", "-d", "--wait", "postgres")
    elif args.operation == "bootstrap":
        compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "flowbridge_admin",
            "-d",
            "postgres",
            "-f",
            "/docker-entrypoint-initdb.d/010-databases.sql",
        )
    elif args.operation in ("upgrade", "check", "status"):
        from scripts.database.cli import main as database_cli

        raise SystemExit(database_cli([args.operation]))
    elif args.operation == "hasura":
        compose("--profile", "integration", "up", "-d", "hasura")
    elif args.operation == "readers":
        from sqlalchemy import text

        from scripts.database.engine import make_engine

        for domain, views in PRODUCT_VIEWS.items():
            engine = make_engine(domain)
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {domain}_reader"))
                    for view in views:
                        conn.execute(text(f"GRANT SELECT ON public.{view} TO {domain}_reader"))
            finally:
                engine.dispose()
        print("Reader SQL privileges granted on exactly six product views")
    elif args.operation == "products":
        from scripts.database.products import export_drafts

        export_drafts()
    elif args.operation == "verify":
        verify_local()
    elif args.operation == "restart":
        compose("restart", "postgres")
        compose("up", "-d", "--wait", "postgres")
    else:
        compose("stop", "postgres")


def verify_local():
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from scripts.database.engine import make_engine

    databases = ["sales_db", "warehouse_db", "logistics_db", "dxos_core_db", "hasura_metadata_db"]
    engine = make_engine("admin", hide_parameters=True)
    try:
        with engine.connect() as conn:
            version = int(conn.scalar(text("SHOW server_version_num")))
            assert 160000 <= version < 170000, "PostgreSQL 16 required"
            actual = set(conn.scalars(text("SELECT datname FROM pg_database")))
            assert set(databases) <= actual, "Missing independent databases"
            for domain in (*BUSINESS, "core", "hasura"):
                owned = {"core": "dxos_core_db", "hasura": "hasura_metadata_db"}.get(
                    domain, f"{domain}_db"
                )
                for role in [
                    f"{domain}_owner",
                    *([f"{domain}_reader"] if domain in BUSINESS else []),
                ]:
                    flags = conn.execute(
                        text(
                            "SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles "
                            "WHERE rolname=:role"
                        ),
                        {"role": role},
                    ).one()
                    assert not any(flags), f"Excess role privilege: {role}"
                    for db in databases:
                        allowed = conn.scalar(
                            text("SELECT has_database_privilege(:role,:db,'CONNECT')"),
                            {"role": role, "db": db},
                        )
                        assert allowed == (db == owned), f"Database CONNECT scope mismatch: {role}"
    finally:
        engine.dispose()
    values = dotenv_values(RUNTIME / "host.env")
    for domain, views in PRODUCT_VIEWS.items():
        url = make_url(values[f"{domain.upper()}_READ_DATABASE_URL"]).set(
            drivername="postgresql+psycopg"
        )
        engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10})
        try:
            with engine.connect() as conn:
                assert conn.connection.driver_connection.pgconn.ssl_in_use, "Reader TLS required"
                for view in views:
                    conn.execute(text(f"SELECT * FROM public.{view} LIMIT 1"))
                    assert not conn.scalar(
                        text("SELECT has_table_privilege(current_user,:view,'INSERT')"),
                        {"view": f"public.{view}"},
                    )
                private_table = {
                    "sales": "customers",
                    "warehouse": "products",
                    "logistics": "shipments",
                }[domain]
                assert not conn.scalar(
                    text("SELECT has_table_privilege(current_user,:table,'SELECT')"),
                    {"table": f"public.{private_table}"},
                )
        finally:
            engine.dispose()
    print("PASS: PostgreSQL 16, five independent DBs, eight scoped roles, TLS view-only readers")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        # Commands contain file paths and fixed identifiers, never credentials.
        raise SystemExit(str(exc)) from None
