"""Explicit bootstrap of the approved Flowable-only schema and role; never Core."""

import argparse
import json
import secrets
import shutil
from pathlib import Path

from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".tmp/flowable-integration"
ROLE = "fb_flowable_runtime"
SCHEMA = "flowable_runtime"


def prepare(ca_file: Path | None = None, *, allow_unverified_tls_for_test=False):
    if ca_file is None and not allow_unverified_tls_for_test:
        raise ValueError("Provide the Supabase CA or explicitly approve test-only TLS require")
    if ca_file is not None:
        ca_file = ca_file.resolve(strict=True)
        if not ca_file.is_file() or "-----BEGIN CERTIFICATE-----" not in ca_file.read_text(
            encoding="ascii"
        ):
            raise ValueError("Provide the Supabase project CA in PEM format")
    ssl_mode = "verify-full" if ca_file else "require"
    values = dotenv_values(ROOT / ".env")
    url = make_url(values["DXOS_ADMIN_URL"])
    if url.database != "postgres" or url.port not in {None, 5432}:
        raise ValueError("Use the approved existing postgres database and session connection")
    target = RUNTIME.resolve()
    if not target.is_relative_to((ROOT / ".tmp").resolve()):
        raise ValueError("Runtime directory is outside workspace")
    target.mkdir(parents=True, exist_ok=True)
    if (target / "docker.env").exists():
        raise ValueError("Runtime config already exists; reuse it instead of rotating credentials")
    (target / "tls").mkdir(exist_ok=True)
    if ca_file:
        shutil.copyfile(ca_file, target / "tls/postgres-ca.pem")
    password, rest_password = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
    engine = create_engine(
        url,
        hide_parameters=True,
        connect_args={
            "sslmode": ssl_mode,
            **({"sslrootcert": str(ca_file)} if ca_file else {}),
            "connect_timeout": 10,
        },
    )
    try:
        with engine.begin() as connection:
            if connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
                {"schema": SCHEMA},
            ) or connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": ROLE}
            ):
                raise ValueError(
                    "Schema or role already exists; refusing ownership/credential changes"
                )
            create_role = (
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOINHERIT NOBYPASSRLS"
                )
                .format(sql.Identifier(ROLE), sql.Literal(password))
                .as_string()
            )
            connection.exec_driver_sql(create_role)
            connection.exec_driver_sql(
                sql.SQL("GRANT {} TO CURRENT_USER WITH INHERIT FALSE")
                .format(sql.Identifier(ROLE))
                .as_string()
            )
            connection.exec_driver_sql(
                sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}")
                .format(sql.Identifier(SCHEMA), sql.Identifier(ROLE))
                .as_string()
            )
            connection.exec_driver_sql(
                sql.SQL("REVOKE {} FROM CURRENT_USER").format(sql.Identifier(ROLE)).as_string()
            )
            connection.exec_driver_sql(
                sql.SQL("GRANT CONNECT ON DATABASE postgres TO {}")
                .format(sql.Identifier(ROLE))
                .as_string()
            )
            if connection.scalar(
                text("SELECT has_schema_privilege(:role,'public','CREATE')"), {"role": ROLE}
            ):
                raise ValueError(
                    "PUBLIC grants CREATE outside the approved schema; refusing startup"
                )
        username = ROLE + "." + url.username.rsplit(".", 1)[1] if "." in url.username else ROLE
        jdbc = (
            f"jdbc:postgresql://{url.host}:{url.port or 5432}/postgres?"
            f"sslmode={ssl_mode}"
            + ("&sslrootcert=/run/tls/postgres-ca.pem" if ca_file else "")
            + "&currentSchema=flowable_runtime"
        )
        config = {
            "FLOWBRIDGE_FLOWABLE_JDBC_URL": jdbc,
            "FLOWBRIDGE_FLOWABLE_DB_USER": username,
            "FLOWBRIDGE_FLOWABLE_DB_PASSWORD": password,
            "FLOWBRIDGE_FLOWABLE_ADMIN_USER": "rest-admin",
            "FLOWBRIDGE_FLOWABLE_ADMIN_PASSWORD": rest_password,
            "FLOWBRIDGE_FLOWABLE_RUNTIME_DIR": target.as_posix(),
            "FLOWBRIDGE_FLOWABLE_BASE_URL": "http://127.0.0.1:18081/flowable-rest/service",
            "FLOWBRIDGE_FLOWABLE_USERNAME": "rest-admin",
            "FLOWBRIDGE_FLOWABLE_PASSWORD": rest_password,
        }
        (target / "docker.env").write_text(
            "\n".join(key + "=" + json.dumps(value) for key, value in config.items()) + "\n",
            encoding="utf-8",
        )
        (target / "docker.env").chmod(0o600)
        print("Prepared Flowable-only schema/role and ignored runtime config; TLS", ssl_mode)
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-flowable-schema-role", action="store_true", required=True)
    tls = parser.add_mutually_exclusive_group(required=True)
    tls.add_argument("--ca-file", type=Path)
    tls.add_argument("--allow-unverified-tls-for-test", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.ca_file, allow_unverified_tls_for_test=args.allow_unverified_tls_for_test)
    except Exception as error:
        print("Flowable bootstrap stopped:", type(error).__name__)
        raise SystemExit(1) from None
