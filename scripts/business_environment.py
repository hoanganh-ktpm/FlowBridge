"""Local-only business workflow bootstrap; never reads the old Supabase .env."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa
from dotenv import dotenv_values
from psycopg import sql

from scripts.database.engine import make_engine
from scripts.database.local import RUNTIME as POSTGRES_RUNTIME
from scripts.database.local import load_local
from scripts.database.registry import REPOSITORY_ROOT
from scripts.docker_env import write_env
from scripts.e2e_environment import tokens

RUNTIME = REPOSITORY_ROOT / ".tmp/business-workflow"
COMPOSE = REPOSITORY_ROOT / "infra/flowable/docker-compose.local.yml"


def historical_snapshot():
    from contextlib import ExitStack

    from scripts.database.simulation import SimulationConfig, simulate
    from scripts.database.synthetic import read_rows, snapshot_hash

    config = SimulationConfig(
        **json.loads((POSTGRES_RUNTIME / "synthetic-run.json").read_text())["config"]
    )
    org = simulate(config)["sales"]["customers"][0]["org_id"]
    with ExitStack() as stack:
        connections = {}
        for domain in ("sales", "warehouse", "logistics"):
            engine = make_engine(domain)
            stack.callback(engine.dispose)
            connections[domain] = stack.enter_context(engine.connect())
        rows = read_rows(connections, org_id=org, exclude_workflow=True)
        return {
            d: {"hash": snapshot_hash(tables), "rows": sum(map(len, tables.values()))}
            for d, tables in rows.items()
        }


def prepare():
    load_local()
    baseline = historical_snapshot()
    run_id = "wf_e2e_" + uuid4().hex[:12]
    directory = REPOSITORY_ROOT / ".tmp/docker-integration/runs" / run_id
    directory.mkdir(parents=True)
    state = {
        "run_id": run_id,
        "org_id": str(uuid4()),
        "warehouse_id": str(uuid4()),
        "products": [str(uuid4()), str(uuid4())],
        "evidence": {},
        "imports": {},
        "historical_before": baseline,
    }
    (directory / "resources.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    (RUNTIME / "current-run.json").write_text(json.dumps({"directory": str(directory)}))
    from scripts.database.registry import metadata_for

    metadata = metadata_for("warehouse")
    t = {table.name: table for table in metadata.sorted_tables}
    org, wh = UUID(state["org_id"]), UUID(state["warehouse_id"])
    now = datetime.now(UTC)
    engine = make_engine("warehouse", hide_parameters=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                t["warehouses"]
                .insert()
                .values(id=wh, org_id=org, warehouse_code=run_id, name=run_id)
            )
            receipt = uuid4()
            conn.execute(
                t["goods_receipts"]
                .insert()
                .values(
                    id=receipt,
                    org_id=org,
                    warehouse_id=wh,
                    receipt_number=run_id,
                    source_type="OPENING_BALANCE",
                    status="POSTED",
                    received_at=now,
                    posted_at=now,
                    received_by_subject="wf_e2e_bootstrap",
                )
            )
            for i, product in enumerate(state["products"], 1):
                pid = UUID(product)
                conn.execute(
                    t["products"]
                    .insert()
                    .values(
                        id=pid, org_id=org, sku=f"{run_id}_{i}", name=f"Workflow E2E {i}", uom="EA"
                    )
                )
                conn.execute(
                    t["inventory_balances"]
                    .insert()
                    .values(
                        id=uuid4(),
                        org_id=org,
                        warehouse_id=wh,
                        product_id=pid,
                        on_hand_quantity=100,
                        reserved_quantity=0,
                    )
                )
                line = uuid4()
                conn.execute(
                    t["goods_receipt_lines"]
                    .insert()
                    .values(
                        id=line,
                        org_id=org,
                        receipt_id=receipt,
                        product_id=pid,
                        line_no=i,
                        received_quantity=100,
                        accepted_quantity=100,
                    )
                )
                conn.execute(
                    t["stock_movements"]
                    .insert()
                    .values(
                        id=uuid4(),
                        org_id=org,
                        warehouse_id=wh,
                        product_id=pid,
                        movement_type="RECEIPT",
                        quantity_delta=100,
                        goods_receipt_line_id=line,
                        actor_subject="wf_e2e_bootstrap",
                        correlation_id=receipt,
                        occurred_at=now,
                    )
                )
    finally:
        engine.dispose()
    configure_run(directory, state)
    print("Prepared isolated business records:", run_id)
    return directory


def configure_run(directory, state):
    local = dotenv_values(POSTGRES_RUNTIME / "host.env")
    oidc = {
        k: v
        for k, v in dotenv_values(REPOSITORY_ROOT / ".tmp/docker-integration/core.env").items()
        if k.startswith("FLOWBRIDGE_OIDC_")
    }
    realm = json.loads((REPOSITORY_ROOT / ".tmp/docker-integration/secrets.json").read_text())
    secrets_config = json.loads((RUNTIME / "secrets.json").read_text())
    auth = tokens()
    subjects = realm["users"]
    services = {
        "sales": "http://127.0.0.1:18101",
        "warehouse": "http://127.0.0.1:18102",
        "logistics": "http://127.0.0.1:18103",
    }
    permissions = {
        "sales": {
            "sales": ["orders.create", "orders.read", "orders.status"],
            "outsider": ["orders.read"],
        },
        "warehouse": {
            "sales": ["stock.reserve", "reservations.read"],
            "outsider": ["inventory.read", "reservations.read", "stock.pack", "stock.handoff"],
            "it": ["reservations.read"],
        },
        "logistics": {
            "sales": ["shipments.create", "shipments.read"],
            "outsider": ["shipments.read"],
            "it": ["shipments.read", "shipments.status"],
        },
    }
    for domain in services:
        bindings = {
            subjects[label]: {"org_id": state["org_id"], "permissions": grants}
            for label, grants in permissions[domain].items()
        }
        write_env(
            directory / (domain + ".env"),
            {
                **oidc,
                "FLOWBRIDGE_BUSINESS_DATABASE_URL": local[f"DXOS_{domain.upper()}_URL"],
                "FLOWBRIDGE_BUSINESS_SERVICE_BASES": json.dumps(
                    {**services, "workflow": "http://127.0.0.1:18000"}
                ),
                "FLOWBRIDGE_BUSINESS_ACTOR_BINDINGS": json.dumps(bindings),
            },
        )
    bindings = {}
    for domain, table, op, status, field, kind in [
        ("sales", "wf_e2e_orders", "INSERT", "CONFIRMED", "id", "sales.workflow_created"),
        (
            "logistics",
            "wf_e2e_shipments",
            "UPDATE",
            "DELIVERED",
            "sales_order_ref",
            "shipment.delivered",
        ),
    ]:
        bindings[f"{state['run_id']}_{domain}"] = {
            "source": f"wf_e2e_{domain}_events",
            "schema": "public",
            "table": table,
            "operation": op,
            "status": status,
            "order_field": field,
            "event_type": kind,
            "org_ids": [state["org_id"]],
        }
    core = {
        **oidc,
        **local,
        "FLOWBRIDGE_WORKFLOW_ENABLED": "true",
        "FLOWBRIDGE_FLOWABLE_BASE_URL": "http://127.0.0.1:18083/flowable-rest/service",
        "FLOWBRIDGE_FLOWABLE_USERNAME": "rest-admin",
        "FLOWBRIDGE_FLOWABLE_PASSWORD": secrets_config["FLOWABLE_REST_PASSWORD"],
        "FLOWBRIDGE_WORKFLOW_ALLOWED_WORKFLOWS": json.dumps(
            ["order_fulfillment", "wf_e2e_event_start"]
        ),
        "FLOWBRIDGE_WORKFLOW_TASK_ASSIGNEE_SUBJECTS": json.dumps([subjects["outsider"]]),
        "FLOWBRIDGE_WORKFLOW_SERVICE_BASES": json.dumps(services),
        "FLOWBRIDGE_WORKFLOW_ALLOWED_HTTP_HOSTS": '["127.0.0.1"]',
        "FLOWBRIDGE_WORKFLOW_ENGINE_TOKEN": auth["sales"],
        "FLOWBRIDGE_WORKFLOW_EVENT_WEBHOOK_SECRET": secrets_config["EVENT_HMAC_SECRET"],
        "FLOWBRIDGE_WORKFLOW_HASURA_RELAY_HEADER_SECRET": secrets_config["RELAY_HEADER_SECRET"],
        "FLOWBRIDGE_WORKFLOW_HASURA_RELAY_BINDINGS": json.dumps(bindings),
        "FLOWBRIDGE_WORKFLOW_EVENT_SUBSCRIPTIONS": json.dumps(
            [
                {
                    "subscription_id": state["run_id"] + "_start",
                    "event_type": "sales.workflow_created",
                    "workflow_key": "wf_e2e_event_start",
                    "mode": "START",
                },
                {
                    "subscription_id": state["run_id"] + "_deliver",
                    "event_type": "shipment.delivered",
                    "workflow_key": "order_fulfillment",
                    "mode": "CORRELATE",
                    "message_name": "shipment.delivered",
                },
            ]
        ),
    }
    write_env(directory / "core.env", core)


def prepare_event_sources():
    """DDL only for isolated E2E tables and their Hasura capture catalogs."""
    load_local()
    secret_file = RUNTIME / "secrets.json"
    state = json.loads(secret_file.read_text())
    env = {"WF_E2E_EVENT_HEADER_SECRET": state["RELAY_HEADER_SECRET"]}
    from scripts.database.config import database_url

    for domain, table in (("sales", "wf_e2e_orders"), ("logistics", "wf_e2e_shipments")):
        role = f"wf_e2e_{domain}_events"
        state.setdefault(role, secrets.token_hex(24))
        admin = sa.create_engine(
            database_url("admin").set(database=f"{domain}_db"), hide_parameters=True
        )
        try:
            with admin.begin() as conn:
                if not conn.scalar(
                    sa.text("SELECT 1 FROM pg_roles WHERE rolname=:name"), {"name": role}
                ):
                    conn.exec_driver_sql(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOINHERIT PASSWORD {}"
                        )
                        .format(sql.Identifier(role), sql.Literal(state[role]))
                        .as_string()
                    )
                owner = conn.scalar(
                    sa.text(
                        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace "
                        "WHERE nspname='hdb_catalog'"
                    )
                )
                if owner and owner != role:
                    raise ValueError(f"Refusing to take over {domain} Hasura capture catalog")
                conn.exec_driver_sql(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS hdb_catalog AUTHORIZATION {}")
                    .format(sql.Identifier(role))
                    .as_string()
                )
                conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pgcrypto")
                conn.exec_driver_sql(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
                    .format(sql.Identifier(f"{domain}_db"), sql.Identifier(role))
                    .as_string()
                )
                conn.exec_driver_sql(
                    sql.SQL("GRANT USAGE ON SCHEMA public TO {}")
                    .format(sql.Identifier(role))
                    .as_string()
                )
                conn.exec_driver_sql(
                    sql.SQL("GRANT SELECT,TRIGGER ON public.{} TO {}")
                    .format(sql.Identifier(table), sql.Identifier(role))
                    .as_string()
                )
        finally:
            admin.dispose()
        env[f"WF_E2E_{domain.upper()}_EVENTS_URL"] = sa.engine.URL.create(
            "postgresql",
            username=role,
            password=state[role],
            host="postgres",
            port=5432,
            database=f"{domain}_db",
            query={"sslmode": "require"},
        ).render_as_string(hide_password=False)
    secret_file.write_text(json.dumps(state), encoding="utf-8")
    write_env(RUNTIME / "hasura.env", env)
    print("E2E event roles own only hdb_catalog; SELECT/TRIGGER limited to two new tables")


def finish_event_sources():
    from scripts.database.config import database_url

    for domain in ("sales", "logistics"):
        engine = sa.create_engine(
            database_url("admin").set(database=f"{domain}_db"), hide_parameters=True
        )
        try:
            with engine.begin() as conn:
                # Hasura's generated helper is SECURITY INVOKER. The application
                # writer needs only usage + INSERT to commit the capture record.
                conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA hdb_catalog TO {domain}_owner")
                conn.exec_driver_sql(f"GRANT INSERT ON hdb_catalog.event_log TO {domain}_owner")
        finally:
            engine.dispose()


class BusinessProcesses:
    def __init__(self, directory):
        self.directory = directory
        self.processes = []

    def start(self, domain):
        port = {"sales": 18101, "warehouse": 18102, "logistics": 18103}[domain]
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Refusing to use occupied Business API port {port}")
        env = {**os.environ, **dotenv_values(self.directory / (domain + ".env"))}
        service = REPOSITORY_ROOT / "services" / (domain + "-api")
        code = (
            f"import sys;sys.path.insert(0,{str(service)!r});import uvicorn;"
            f"uvicorn.run('app.runtime:create_app',factory=True,host='127.0.0.1',"
            f"port={port},log_level='warning')"
        )
        log = (self.directory / (domain + ".log")).open("ab")
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=REPOSITORY_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.processes.append((domain, process, log))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"{domain} failed; inspect private service log")
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"{domain} readiness timeout")

    def close(self):
        for _, process, log in self.processes:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()
        self.processes = []

    def stop(self, domain):
        for entry in list(self.processes):
            label, process, log = entry
            if label == domain:
                process.terminate()
                process.wait(timeout=5)
                log.close()
                self.processes.remove(entry)


def configure():
    load_local()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    state_file = RUNTIME / "secrets.json"
    state = (
        json.loads(state_file.read_text())
        if state_file.exists()
        else {
            key: secrets.token_hex(24)
            for key in (
                "FLOWABLE_DB_PASSWORD",
                "FLOWABLE_REST_PASSWORD",
                "RELAY_HEADER_SECRET",
                "EVENT_HMAC_SECRET",
            )
        }
    )
    state_file.write_text(json.dumps(state), encoding="utf-8")
    auth = tokens()  # Existing local test realm; no user/realm mutations.
    write_env(RUNTIME / "docker.env", {**state, "WORKFLOW_ENGINE_TOKEN": auth["sales"]})
    engine = make_engine("admin", isolation_level="AUTOCOMMIT", hide_parameters=True)
    try:
        with engine.connect() as conn:
            if not conn.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='wf_engine'")):
                conn.exec_driver_sql(
                    sql.SQL(
                        "CREATE ROLE wf_engine LOGIN NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE PASSWORD {}"
                    )
                    .format(sql.Literal(state["FLOWABLE_DB_PASSWORD"]))
                    .as_string()
                )
            if not conn.scalar(sa.text("SELECT 1 FROM pg_database WHERE datname='flowable_db'")):
                conn.exec_driver_sql("CREATE DATABASE flowable_db OWNER wf_engine")
            owner = conn.scalar(
                sa.text(
                    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='flowable_db'"
                )
            )
            if owner != "wf_engine":
                raise ValueError("Refusing to take over an unrelated flowable_db")
            conn.exec_driver_sql("REVOKE ALL ON DATABASE flowable_db FROM PUBLIC")
    finally:
        engine.dispose()
    print("Local flowable_db and separate credentials ready; existing five DBs unchanged")


def compose(*args):
    subprocess.run(
        ["docker", "compose", "--env-file", str(RUNTIME / "docker.env"), "-f", str(COMPOSE), *args],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("configure", "prepare", "event-sources", "up", "restart", "stop")
    )
    args = parser.parse_args()
    if args.operation == "configure":
        configure()
    elif args.operation == "prepare":
        prepare()
    elif args.operation == "event-sources":
        prepare_event_sources()
    elif args.operation == "up":
        compose("config", "--quiet")
        compose("up", "-d")
    else:
        compose(args.operation, "flowable")


if __name__ == "__main__":
    main()
