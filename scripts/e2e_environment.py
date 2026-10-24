"""Explicit test bootstrap on existing Supabase databases; no database/reset operations."""

import argparse
import base64
import json
import secrets
import ssl
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url

from app.core.applications.manifest import source_name
from app.core.db import models
from scripts.integration_utils import ROOT, RUNTIME, engine_for, tokens, write_env


def reader_url(admin_url, role, password):
    project = admin_url.username.partition(".")[2]
    username = role + "." + project if project else role
    return admin_url.set(username=username, password=password)


def create_login(connection, name, password):
    driver = connection.connection.driver_connection
    verifier = driver.pgconn.encrypt_password(
        password.encode(), name.encode(), b"scram-sha-256"
    ).decode()
    with driver.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOINHERIT PASSWORD {}"
            ).format(sql.Identifier(name), sql.Literal(verifier))
        )


def verify_login(url):
    engine = engine_for(url)
    try:
        for attempt in range(10):
            try:
                with engine.connect() as connection:
                    assert connection.scalar(text("SELECT 1")) == 1
                    assert connection.connection.driver_connection.pgconn.ssl_in_use
                return
            except Exception:
                if attempt == 9:
                    raise
                time.sleep(1)
    finally:
        engine.dispose()


def prepare_metadata():
    values = dotenv_values(ROOT / ".env")
    admin_url = make_url(values["DXOS_ADMIN_URL"]).update_query_dict({"sslmode": "require"})
    if (
        admin_url.database != "postgres"
        or not admin_url.host.endswith(".pooler.supabase.com")
        or admin_url.port != 5432
    ):
        raise ValueError("Expected the existing Supabase postgres database/session pooler")
    state_file = RUNTIME / "supabase-metadata.json"
    engine = engine_for(admin_url)
    try:
        if state_file.exists():
            state = json.loads(state_file.read_text())
        else:
            with engine.connect() as connection:
                if connection.scalar(text("SELECT to_regnamespace('hdb_catalog') IS NOT NULL")):
                    raise ValueError(
                        "Existing hdb_catalog is not owned by this test bootstrap; "
                        "supply its reviewed configuration"
                    )
                if (
                    connection.scalar(
                        text(
                            "SELECT n.nspname FROM pg_extension e "
                            "JOIN pg_namespace n ON e.extnamespace=n.oid "
                            "WHERE e.extname='pgcrypto'"
                        )
                    )
                    != "extensions"
                ):
                    raise ValueError(
                        "Expected existing pgcrypto in extensions; no automatic extension changes"
                    )
            state = {
                "role": "fb_e2e_meta_" + uuid4().hex[:12],
                "password": secrets.token_urlsafe(36),
                "created": False,
            }
            state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        if not state["created"]:
            role = engine.dialect.identifier_preparer.quote_identifier(state["role"])
            with engine.begin() as connection:
                create_login(connection, state["role"], state["password"])
                connection.exec_driver_sql("CREATE SCHEMA hdb_catalog")
                connection.exec_driver_sql("GRANT CONNECT ON DATABASE postgres TO " + role)
                connection.exec_driver_sql("GRANT USAGE, CREATE ON SCHEMA hdb_catalog TO " + role)
                connection.exec_driver_sql("GRANT USAGE ON SCHEMA extensions TO " + role)
                connection.exec_driver_sql(
                    "ALTER ROLE " + role + " SET search_path TO hdb_catalog, extensions, public"
                )
            state["created"] = True
            state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        target = reader_url(admin_url, state["role"], state["password"])
        verify_login(target)
        existing_config = dict(dotenv_values(RUNTIME / "hasura.env"))
        write_env(
            RUNTIME / "hasura.env",
            {
                **existing_config,
                "HASURA_GRAPHQL_METADATA_DATABASE_URL": target.set(
                    drivername="postgresql"
                ).render_as_string(hide_password=False),
            },
        )
        print(
            "Supabase metadata ready: existing postgres / isolated hdb_catalog; "
            "dedicated non-admin role, TLS"
        )
        print("No database created, no Core/business schema or data modified")
    finally:
        engine.dispose()


def expired_token():
    state = json.loads((RUNTIME / "secrets.json").read_text())
    context = ssl.create_default_context(cafile=str(RUNTIME / "tls/server.crt"))
    origin = "https://localhost:18443"
    with httpx.Client(verify=context, timeout=20) as client:
        response = client.post(
            origin + "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "flowbridge-test-admin",
                "password": state["admin_password"],
            },
        )
        response.raise_for_status()
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        endpoint = origin + "/admin/realms/flowbridge-it/clients"
        response = client.get(endpoint, params={"clientId": "flowbridge-e2e"}, headers=headers)
        response.raise_for_status()
        (original,) = response.json()
        assert original["clientId"] == "flowbridge-e2e"
        original["attributes"] = {
            **original.get("attributes", {}),
            "access.token.lifespan": "600",
        }
        endpoint += "/" + original["id"]
        temporary = {
            **original,
            "attributes": {
                **original.get("attributes", {}),
                "access.token.lifespan": "1",
            },
        }
        try:
            client.put(endpoint, headers=headers, json=temporary).raise_for_status()
            response = client.post(
                origin + "/realms/flowbridge-it/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "flowbridge-e2e",
                    "username": "test-sales",
                    "password": state["user_password"],
                    "scope": "openid",
                },
            )
            response.raise_for_status()
            token = response.json()["access_token"]
        finally:
            client.put(endpoint, headers=headers, json=original).raise_for_status()
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        assert claims["exp"] - claims["iat"] <= 2
        time.sleep(max(0, claims["exp"] + 1 - time.time()))
        return token


def business_snapshot(url):
    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            names = inspect(connection).get_table_names(schema="public")
            quote = connection.dialect.identifier_preparer.quote_identifier
            parts = [
                "SELECT :name" + str(index) + " AS name, count(*) AS count, "
                "md5(COALESCE(jsonb_agg(to_jsonb(entry) "
                "ORDER BY to_jsonb(entry)::text)::text, '[]')) AS hash "
                "FROM public." + quote(name) + " AS entry"
                for index, name in enumerate(names)
            ]
            rows = connection.execute(
                text(" UNION ALL ".join(parts)),
                {"name" + str(index): name for index, name in enumerate(names)},
            ).mappings()
            return {row["name"]: {"count": row["count"], "hash": row["hash"]} for row in rows}
    finally:
        engine.dispose()


def prepare_run():
    from tests.core.persistence.test_onboarding import submission

    pointer = RUNTIME / "current-run.json"
    if pointer.exists():
        previous = Path(json.loads(pointer.read_text())["directory"]).resolve()
        assert previous.is_relative_to((RUNTIME / "runs").resolve())
        assert json.loads((previous / "resources.json").read_text()).get("cleaned"), (
            "An uncleaned run exists; resume or clean it before creating another fixture"
        )
    values = dotenv_values(ROOT / ".env")
    urls = {
        name: make_url(values["DXOS_" + name.upper() + "_URL"]).update_query_dict(
            {"sslmode": "require"}
        )
        for name in ("core", "warehouse", "sales", "logistics")
    }
    assert [urls[name].database for name in urls] == [
        "dxos_core_db",
        "warehouse_db",
        "sales_db",
        "logistics_db",
    ]
    assert len({(url.host, url.port, url.database) for url in urls.values()}) == 4
    tokens()
    state = json.loads((RUNTIME / "secrets.json").read_text())
    run_id = uuid4().hex[:12]
    directory = RUNTIME / "runs" / run_id
    directory.mkdir(parents=True)
    identity_file = RUNTIME / "core-identities.json"
    if identity_file.exists():
        identities = json.loads(identity_file.read_text())
    else:
        identities = {name: str(uuid4()) for name in ("it", "sales", "outsider", "disabled")}
        identity_file.write_text(json.dumps(identities), encoding="utf-8")
    resources = {
        "run_id": run_id,
        "principal_ids": identities,
        "departments": {
            name: {"id": str(uuid4()), "code": "fb-e2e-" + name + "-" + run_id}
            for name in ("warehouse", "sales", "outsider")
        },
        "membership_ids": {name: str(uuid4()) for name in ("sales", "outsider")},
        "fixture": {"id": str(uuid4()), "org_id": str(uuid4()), "sku": "DXOS-E2E-" + run_id},
        "apps": {name: "fb-e2e-" + name + "-" + run_id for name in ("warehouse", "sales")},
        "baseline": {
            name: business_snapshot(urls[name]) for name in ("warehouse", "sales", "logistics")
        },
        "evidence": {},
        "created_fixture": False,
    }
    ledger = directory / "resources.json"
    ledger.write_text(json.dumps(resources, indent=2), encoding="utf-8")
    reader_file = RUNTIME / "source-readers.json"
    readers = (
        json.loads(reader_file.read_text())
        if reader_file.exists()
        else {
            name: {
                "role": "fb_e2e_" + name + "_" + uuid4().hex[:10],
                "password": secrets.token_urlsafe(36),
                "created": False,
            }
            for name in ("warehouse", "sales")
        }
    )
    reader_file.write_text(json.dumps(readers), encoding="utf-8")
    source_urls = {}
    for name, columns, table in (
        ("warehouse", "id, sku, name, uom", "products"),
        ("sales", "id", "customers"),
    ):
        engine = engine_for(urls[name])
        try:
            with engine.begin() as connection:
                assert not connection.scalar(
                    text("SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass(:name)"),
                    {"name": "public." + table},
                ), "RLS changes are not permitted"
                if not readers[name]["created"]:
                    role = connection.dialect.identifier_preparer.quote_identifier(
                        readers[name]["role"]
                    )
                    create_login(connection, readers[name]["role"], readers[name]["password"])
                    database = connection.dialect.identifier_preparer.quote_identifier(
                        urls[name].database
                    )
                    connection.exec_driver_sql(
                        "GRANT CONNECT ON DATABASE " + database + " TO " + role
                    )
                    connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO " + role)
                    connection.exec_driver_sql(
                        "GRANT SELECT (" + columns + ") ON TABLE public." + table + " TO " + role
                    )
            readers[name]["created"] = True
            reader_file.write_text(json.dumps(readers), encoding="utf-8")
        finally:
            engine.dispose()
        source_urls[name] = reader_url(urls[name], readers[name]["role"], readers[name]["password"])
        verify_login(source_urls[name])
    engine = engine_for(urls["warehouse"])
    try:
        with engine.begin() as connection:
            fixture = resources["fixture"]
            assert not connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM public.products WHERE id=:id OR sku=:sku)"),
                fixture,
            )
            connection.execute(
                text(
                    "INSERT INTO public.products(id, org_id, sku, name, uom, unit_cost) "
                    "VALUES (:id, :org_id, :sku, 'DXOS E2E source 20', 'test', 12345)"
                ),
                fixture,
            )
        resources["created_fixture"] = True
        ledger.write_text(json.dumps(resources, indent=2), encoding="utf-8")
    finally:
        engine.dispose()
    engine = engine_for(urls["core"])
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "core_0004"
            assert not connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM publication_jobs "
                    "WHERE status IN ('QUEUED','RETRY','RUNNING'))"
                )
            ), "Pending jobs must be reviewed before starting a test worker"
            for name, identifier in identities.items():
                existing = (
                    connection.execute(
                        select(models.principals).where(
                            models.principals.c.issuer
                            == "https://localhost:18443/realms/flowbridge-it",
                            models.principals.c.subject == state["users"][name],
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing:
                    assert str(existing["id"]) == identifier, (
                        "Refusing to change an unrelated principal"
                    )
                    connection.execute(
                        models.principals.update()
                        .where(models.principals.c.id == identifier)
                        .values(status="DISABLED" if name == "disabled" else "ACTIVE")
                    )
                else:
                    connection.execute(
                        models.principals.insert().values(
                            id=identifier,
                            issuer="https://localhost:18443/realms/flowbridge-it",
                            subject=state["users"][name],
                            kind="human",
                            display_name="Docker E2E " + name,
                            platform_role="PLATFORM_ADMIN" if name == "it" else "NONE",
                            status="DISABLED" if name == "disabled" else "ACTIVE",
                        )
                    )
            for name, department in resources["departments"].items():
                connection.execute(
                    models.departments.insert().values(
                        id=department["id"],
                        code=department["code"],
                        name="E2E " + name + " " + run_id,
                    )
                )
            for name, identifier in resources["membership_ids"].items():
                connection.execute(
                    models.department_memberships.insert().values(
                        id=identifier,
                        principal_id=identities[name],
                        department_id=resources["departments"][name]["id"],
                        membership_role="STAFF",
                    )
                )
            connection.execute(
                models.audit_events.insert().values(
                    id=str(uuid4()),
                    occurred_at=text("now()"),
                    actor_kind="system",
                    action="e2e.identity-bootstrap",
                    resource_type="test_run",
                    resource_ref=run_id,
                    outcome="SUCCESS",
                    request_id=run_id,
                )
            )
    finally:
        engine.dispose()
    bodies = {}
    for name, table, columns in (
        (
            "warehouse",
            "products",
            [
                ("id", "UUID"),
                ("sku", "VARCHAR(80)"),
                ("name", "VARCHAR(255)"),
                ("uom", "VARCHAR(30)"),
            ],
        ),
        ("sales", "customers", [("id", "UUID")]),
    ):
        bodies[name] = submission(
            source_urls[name],
            resources["departments"][name]["code"],
            resources["apps"][name],
            object_name=table,
            secret_ref="e2e-" + name + "-reader",
            columns=[
                {
                    "column": column,
                    "name": column,
                    "physical_type": physical,
                    "wire_type": "string",
                    "nullable": False,
                    "description": None,
                }
                for column, physical in columns
            ],
        )
        (directory / (name + ".json")).write_text(
            json.dumps(bodies[name], indent=2), encoding="utf-8"
        )
    core_config = dict(dotenv_values(RUNTIME / "core.env"))
    core_config.update(
        {
            "FLOWBRIDGE_HASURA_ALLOWED_DATABASE_HOSTS": json.dumps(
                list({url.host for url in source_urls.values()})
            ),
            "FLOWBRIDGE_HASURA_ALLOWED_SOURCES": json.dumps(
                [source_name(key, "pg") for key in resources["apps"].values()]
            ),
            "FLOWBRIDGE_HASURA_SECRET_BINDINGS": json.dumps(
                {
                    "e2e-" + name + "-reader": "FB_E2E_" + name.upper() + "_URL"
                    for name in source_urls
                }
            ),
            "FLOWBRIDGE_CORE_SOURCE_SCOPES": json.dumps(
                {
                    "e2e-warehouse-reader": [{"schema": "public", "name": "products"}],
                    "e2e-sales-reader": [{"schema": "public", "name": "customers"}],
                }
            ),
            "FLOWBRIDGE_CORE_WORKER_POLL_SECONDS": "0.2",
        }
    )
    hasura_config = dict(dotenv_values(RUNTIME / "hasura.env"))
    for name, url in source_urls.items():
        raw = url.set(drivername="postgresql").render_as_string(hide_password=False)
        core_config["FB_E2E_" + name.upper() + "_URL"] = raw
        hasura_config["FB_E2E_" + name.upper() + "_URL"] = raw
    write_env(directory / "core.env", core_config)
    write_env(RUNTIME / "hasura.env", hasura_config)
    (RUNTIME / "current-run.json").write_text(
        json.dumps({"directory": str(directory)}), encoding="utf-8"
    )
    print("Prepared E2E run", run_id, "on existing Core/Warehouse/Sales; ledger retained locally")
    return directory


def approve_table_readers():
    values = dotenv_values(ROOT / ".env")
    path = RUNTIME / "source-readers.json"
    readers = json.loads(path.read_text())
    for name, table in (("warehouse", "products"), ("sales", "customers")):
        reader = readers[name]
        assert reader["created"] and reader["role"].startswith("fb_e2e_" + name + "_")
        url = make_url(values["DXOS_" + name.upper() + "_URL"])
        assert url.database == name + "_db"
        engine = engine_for(url)
        try:
            with engine.begin() as connection:
                role = connection.dialect.identifier_preparer.quote_identifier(reader["role"])
                connection.exec_driver_sql("GRANT SELECT ON TABLE public." + table + " TO " + role)
            reader["table_select_approved"] = True
            path.write_text(json.dumps(readers), encoding="utf-8")
        finally:
            engine.dispose()
    print("Approved SELECT on exactly products/customers for two owned test readers; no writes/DDL")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["metadata", "prepare", "approve-table-readers"])
    parser.add_argument("--allow-supabase-test-writes", action="store_true", required=True)
    args = parser.parse_args()
    if args.action == "metadata":
        prepare_metadata()
    elif args.action == "approve-table-readers":
        approve_table_readers()
    else:
        prepare_run()


if __name__ == "__main__":
    main()
