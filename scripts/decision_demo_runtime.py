# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit local decision demo composition; credentials stay in ignored .tmp."""

import argparse
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import httpx
import psycopg
import sqlalchemy as sa
from dotenv import dotenv_values
from psycopg import sql

from scripts.decision_demo_data import DIRECTORY, ROOT
from scripts.decision_local_identity import BASE, REALM, checked, client_definition
from scripts.integration_utils import RUNTIME as IDENTITY_RUNTIME
from scripts.integration_utils import write_env

ISSUER = f"{BASE}/realms/{REALM}"
PORTS = {
    "core": 18000,
    "workspace": 18090,
    "sales": 18084,
    "warehouse": 18085,
    "logistics": 18086,
    "portal": 5174,
}
ROLES = {
    "coordinator": "Người điều phối",
    "warehouse": "Phụ trách kho",
    "logistics": "Điều phối vận chuyển",
    "manager": "Quản lý phê duyệt",
}


def state():
    return json.loads((DIRECTORY / "identity-resources.json").read_text(encoding="utf-8"))


def local_configuration():
    from scripts.database.config import database_url
    from scripts.database.local import load_local

    load_local()
    for domain in ("core", "sales", "warehouse", "logistics"):
        target = database_url(domain)
        if (target.host, target.port, target.username, target.database) != (
            "127.0.0.1",
            55432,
            domain + "_owner",
            "dxos_core_db" if domain == "core" else domain + "_db",
        ):
            raise ValueError("Demo runtime only supports the guarded local owner databases")
    return dict(dotenv_values(ROOT / ".tmp/local-postgres/host.env"))


def ensure_membership(conn, org, principal, department):
    from sqlalchemy.dialects.postgresql import insert

    from app.core.db import models

    conn.execute(
        insert(models.department_memberships)
        .values(
            id=uuid5(UUID(str(org)), "membership-principal:" + str(principal)),
            principal_id=principal,
            department_id=department,
            membership_role="STAFF",
            status="ACTIVE",
        )
        .on_conflict_do_nothing(index_elements=["principal_id", "department_id"])
    )
    membership = (
        conn.execute(
            sa.select(models.department_memberships).where(
                models.department_memberships.c.principal_id == principal,
                models.department_memberships.c.department_id == department,
            )
        )
        .mappings()
        .one()
    )
    if membership["status"] != "ACTIVE":
        raise ValueError("Demo membership was revoked; no automatic reactivation")


def configure(coordinator_username=None):
    from sqlalchemy.dialects.postgresql import insert

    from app.core.db import models
    from scripts.database.engine import make_engine

    local = local_configuration()
    data = json.loads((DIRECTORY / "data-resources.json").read_text(encoding="utf-8"))
    if set(data["completed_domains"]) != {"sales", "warehouse", "logistics"}:
        raise ValueError("Finish the owned local data seed first")
    org, wh = data["org_id"], data["warehouse_id"]
    ledger_path = DIRECTORY / "identity-resources.json"
    ledger = (
        state()
        if ledger_path.exists()
        else {
            "org_id": org,
            "warehouse_id": wh,
            "issuer": ISSUER,
            "prefix": "decision-demo-" + org[:8] + "-",
            "users": {},
            "clients": {},
            "department_id": str(uuid5(UUID(org), "decision-demo-department")),
        }
    )
    if (ledger["org_id"], ledger["warehouse_id"], ledger["issuer"]) != (org, wh, ISSUER):
        raise ValueError("Existing identity resources belong to another demo profile")
    private_path = DIRECTORY / "credentials.json"
    private = (
        json.loads(private_path.read_text())
        if private_path.exists()
        else {"users": {}, "clients": {}}
    )

    def record():
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        private_path.write_text(json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8")

    record()
    identity_secrets = json.loads((IDENTITY_RUNTIME / "secrets.json").read_text())
    ca = ssl.create_default_context(cafile=str(IDENTITY_RUNTIME / "tls/server.crt"))
    with httpx.Client(base_url=BASE, verify=ca, timeout=30, follow_redirects=False) as http:
        token = checked(
            http.post(
                "/realms/master/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": "flowbridge-test-admin",
                    "password": identity_secrets["admin_password"],
                },
            ),
            200,
        ).json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        users_url, clients_url = f"/admin/realms/{REALM}/users", f"/admin/realms/{REALM}/clients"
        for role, label in ROLES.items():
            name = ledger["prefix"] + role
            if role in ledger["users"]:
                user = checked(
                    http.get(users_url + "/" + ledger["users"][role]["subject"], headers=headers),
                    200,
                ).json()
                if user["username"] != name:
                    raise ValueError("Refusing to adopt or modify an unrelated user")
                continue
            found = checked(
                http.get(users_url, params={"username": name, "exact": True}, headers=headers), 200
            ).json()
            if found:
                raise ValueError(
                    "Existing unrecorded demo username requires explicit reconciliation"
                )
            password = secrets.token_urlsafe(22)
            checked(
                http.post(
                    users_url,
                    headers=headers,
                    json={
                        "username": name,
                        "enabled": True,
                        "firstName": label,
                        "lastName": "Bình Dương",
                        "emailVerified": False,
                        "requiredActions": ["UPDATE_PASSWORD", "CONFIGURE_TOTP"],
                        "credentials": [{"type": "password", "value": password, "temporary": True}],
                    },
                ),
                201,
            )
            user = checked(
                http.get(users_url, params={"username": name, "exact": True}, headers=headers), 200
            ).json()
            if len(user) != 1:
                raise ValueError("Cannot identify the newly owned demo user")
            ledger["users"][role] = {
                "subject": user[0]["id"],
                "username": name,
                "principal_id": str(uuid5(UUID(org), "principal:" + role)),
            }
            private["users"][role] = {"username": name, "initial_password": password}
            record()
        for role in ("executor", "setup"):
            name = ledger["prefix"] + role
            if role in ledger["clients"]:
                item = checked(
                    http.get(clients_url + "/" + ledger["clients"][role]["id"], headers=headers),
                    200,
                ).json()
                if item["clientId"] != name:
                    raise ValueError("Demo service client identity changed")
                continue
            if checked(
                http.get(clients_url, params={"clientId": name}, headers=headers), 200
            ).json():
                raise ValueError(
                    "Existing unrecorded service client requires explicit reconciliation"
                )
            secret = secrets.token_urlsafe(36)
            checked(
                http.post(clients_url, headers=headers, json=client_definition(name, secret)), 201
            )
            item = checked(
                http.get(clients_url, params={"clientId": name}, headers=headers), 200
            ).json()[0]
            user = checked(
                http.get(clients_url + "/" + item["id"] + "/service-account-user", headers=headers),
                200,
            ).json()
            ledger["clients"][role] = {
                "id": item["id"],
                "client_id": name,
                "subject": user["id"],
                "principal_id": str(uuid5(UUID(org), "principal:" + role)),
            }
            private["clients"][role] = {"client_id": name, "secret": secret}
            record()
    if coordinator_username:
        saved = json.loads((ROOT / ".tmp/data-workspace/portal-user.json").read_text())
        if saved.get("username") != coordinator_username or saved.get("issuer") != ISSUER:
            raise ValueError("Only the explicitly recorded local Portal account can be assigned")
        ledger["aliases"] = {
            "coordinator": {
                "username": coordinator_username,
                "subject": saved["subject"],
                "principal_id": saved["principal_id"],
                "existing_user": True,
            }
        }
        record()
    effective_users = {**ledger["users"], **ledger.get("aliases", {})}
    engine = make_engine("core", hide_parameters=True)
    try:
        with engine.begin() as conn:
            dept = UUID(ledger["department_id"])
            conn.execute(
                insert(models.departments)
                .values(
                    id=dept,
                    code="decision_demo_" + org[:8],
                    name="Tổ phối hợp demo Bình Dương",
                    status="ACTIVE",
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            for role, identity in {**effective_users, **ledger["clients"]}.items():
                principal = UUID(identity["principal_id"])
                existing = (
                    conn.execute(
                        sa.select(models.principals).where(models.principals.c.id == principal)
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing and (existing["issuer"], existing["subject"], existing["status"]) != (
                    ISSUER,
                    identity["subject"],
                    "ACTIVE",
                ):
                    raise ValueError("Existing demo principal binding was changed; no reactivation")
                conn.execute(
                    insert(models.principals)
                    .values(
                        id=principal,
                        issuer=ISSUER,
                        subject=identity["subject"],
                        kind="service" if role in ledger["clients"] else "human",
                        display_name=ROLES.get(role, role),
                        service_client_id=identity.get("client_id"),
                        status="ACTIVE",
                        platform_role="PLATFORM_ADMIN" if role == "setup" else "NONE",
                    )
                    .on_conflict_do_nothing(index_elements=["id"])
                )
                ensure_membership(conn, org, principal, dept)
    finally:
        engine.dispose()
    identity = {
        k: v
        for k, v in dotenv_values(IDENTITY_RUNTIME / "core.env").items()
        if k.startswith("FLOWBRIDGE_OIDC_")
    }
    identity["FLOWBRIDGE_OIDC_CA_BUNDLE"] = str(IDENTITY_RUNTIME / "tls/server.crt")
    clients = [item["client_id"] for item in ledger["clients"].values()]
    identity.update(
        FLOWBRIDGE_OIDC_ALLOWED_CLIENTS=json.dumps(["flowbridge-web-a", *clients]),
        FLOWBRIDGE_OIDC_SERVICE_CLIENTS=json.dumps(clients),
    )
    bindings = {
        item["principal_id"]: {"org_id": org, "warehouse_ids": [wh], "roles": [role]}
        for role, item in effective_users.items()
    }
    services = {
        domain: f"http://127.0.0.1:{PORTS[domain]}"
        for domain in ("sales", "warehouse", "logistics")
    }
    workflow_file = DIRECTORY / "flowable.env"
    if not workflow_file.exists():
        password = secrets.token_hex(24)
        admin = sa.engine.make_url(local["DXOS_ADMIN_URL"]).set(drivername="postgresql")
        with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
            if conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname='decision_demo_engine'"
            ).fetchone():
                raise ValueError("Existing engine role without owned config; no password reset")
            conn.execute(
                sql.SQL(
                    "CREATE ROLE decision_demo_engine LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Literal(password))
            )
            conn.execute("CREATE DATABASE decision_demo_flowable_db OWNER decision_demo_engine")
            conn.execute("REVOKE CONNECT ON DATABASE decision_demo_flowable_db FROM PUBLIC")
        write_env(
            workflow_file,
            {
                "SPRING_DATASOURCE_URL": "jdbc:postgresql://postgres:5432/decision_demo_flowable_db?sslmode=require",
                "SPRING_DATASOURCE_USERNAME": "decision_demo_engine",
                "SPRING_DATASOURCE_PASSWORD": password,
                "SERVER_PORT": "18081",
                "FLOWABLE_REST_APP_ADMIN_USER_ID": "decision-demo-admin",
                "FLOWABLE_REST_APP_ADMIN_PASSWORD": secrets.token_hex(24),
                "FLOWABLE_DATABASE_SCHEMA_UPDATE": "true",
                "FLOWABLE_REST_APP_CREATE_DEMO_DEFINITIONS": "false",
                "REST_VARIABLES_ALLOW_SERIALIZABLE": "false",
                "FLOWABLE_REST_APP_SWAGGER_DOCS_ENABLED": "false",
                "SPRING_DATASOURCE_HIKARI_MAXIMUM_POOL_SIZE": "4",
            },
        )
    flowable = dotenv_values(workflow_file)
    core = {
        **local,
        **identity,
        "FLOWBRIDGE_WORKFLOW_ENABLED": "true",
        "FLOWBRIDGE_CORE_WORKER_POLL_SECONDS": "0.2",
        "FLOWBRIDGE_CORE_DATABASE_POOL_SIZE": "8",
        "FLOWBRIDGE_CORS_ORIGINS": '["http://127.0.0.1:5174"]',
        "FLOWBRIDGE_WORKFLOW_ALLOWED_WORKFLOWS": '["supply_handover_exception"]',
        "FLOWBRIDGE_WORKFLOW_DECISION_EXECUTION_PRINCIPAL": ledger["clients"]["executor"][
            "principal_id"
        ],
        "FLOWBRIDGE_WORKFLOW_DECISION_BINDINGS": json.dumps(bindings),
        "FLOWBRIDGE_WORKFLOW_SERVICE_BASES": json.dumps(services),
        "FLOWBRIDGE_WORKFLOW_ALLOWED_HTTP_HOSTS": '["127.0.0.1"]',
        "FLOWBRIDGE_FLOWABLE_BASE_URL": "http://127.0.0.1:18083/flowable-rest/service",
        "FLOWBRIDGE_FLOWABLE_USERNAME": "decision-demo-admin",
        "FLOWBRIDGE_FLOWABLE_PASSWORD": flowable["FLOWABLE_REST_APP_ADMIN_PASSWORD"],
    }
    write_env(DIRECTORY / "core.env", core)
    workspace_db = dotenv_values(ROOT / ".tmp/data-workspace/runtime.env").get(
        "FLOWBRIDGE_WORKSPACE_DATABASE_URL"
    )
    if not workspace_db:
        raise ValueError(
            "Initialize the local Workspace database with scripts.data_workspace init-local first"
        )
    from scripts.decision_test_environment import validate_test_url

    validate_test_url(workspace_db)
    workspace = {
        "FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS": dotenv_values(DIRECTORY / "workspace.env").get(
            "FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS", "[]"
        ),
        "FLOWBRIDGE_WORKSPACE_DATABASE_URL": workspace_db,
        "FLOWBRIDGE_WORKSPACE_CORE_URL": "http://127.0.0.1:18000",
        "FLOWBRIDGE_WORKSPACE_ORG_ID": org,
        "FLOWBRIDGE_WORKSPACE_SCAN_PAGES": "4",
        "FLOWBRIDGE_WORKSPACE_DECISION_BINDINGS": json.dumps(bindings),
        "FLOWBRIDGE_WORKSPACE_WORKFLOW_TOKEN_URL": ISSUER + "/protocol/openid-connect/token",
        "FLOWBRIDGE_WORKSPACE_WORKFLOW_CLIENT_ID": private["clients"]["executor"]["client_id"],
        "FLOWBRIDGE_WORKSPACE_WORKFLOW_CLIENT_SECRET": private["clients"]["executor"]["secret"],
        "FLOWBRIDGE_WORKSPACE_WORKFLOW_CA_FILE": identity["FLOWBRIDGE_OIDC_CA_BUNDLE"],
    }
    write_env(DIRECTORY / "workspace.env", workspace)
    for domain in services:
        actors = {}
        for role, item in effective_users.items():
            permissions = ["operations.events.read"]
            if domain == "sales":
                permissions += ["operations.orders.read"]
                if role == "coordinator":
                    permissions += ["operations.orders.reconcile"]
            elif domain == "warehouse":
                permissions += [
                    "operations.inventory.read",
                    "operations.inbound.read",
                    "operations.reservations.read",
                    "operations.dispatch.read",
                    "operations.handoff_proof.read",
                ]
                if role == "warehouse":
                    permissions += [
                        "operations.preparation.confirm",
                        "operations.inbound.update_eta",
                        "operations.receipts.post",
                        "operations.stock.reserve",
                        "operations.dispatch.prepare",
                        "operations.dispatch.handoff",
                    ]
            else:
                permissions += ["operations.capacity.read", "operations.shipments.read"]
                if role == "logistics":
                    permissions += ["operations.shipments.book", "operations.shipments.reconcile"]
            actors[item["subject"]] = {
                "org_id": org,
                "warehouse_ids": [wh],
                "permissions": permissions,
            }
        actors[ledger["clients"]["executor"]["subject"]] = {
            "org_id": org,
            "warehouse_ids": [wh],
            "permissions": ["operations.orders.read"]
            if domain == "sales"
            else ["operations.handoff_proof.read"]
            if domain == "warehouse"
            else [],
        }
        write_env(
            DIRECTORY / (domain + ".env"),
            {
                **identity,
                "FLOWBRIDGE_BUSINESS_DATABASE_URL": local["DXOS_" + domain.upper() + "_URL"],
                "FLOWBRIDGE_BUSINESS_ACTOR_BINDINGS": json.dumps(actors),
                "FLOWBRIDGE_BUSINESS_SERVICE_BASES": json.dumps(services),
            },
        )
    ledger["configured"] = True
    record()
    print(
        "Configured owned local identities/runtime files. "
        "Initial passwords: .tmp/decision-demo/credentials.json. "
        "Human MFA enrollment remains required."
    )


def process_environment(name):
    values = (
        dotenv_values(DIRECTORY / (name + ".env"))
        if name != "worker"
        else dotenv_values(DIRECTORY / "core.env")
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("FLOWBRIDGE_", "DXOS_"))
        and k not in {"DATABASE_URL", "PGPASSWORD", "PGHOST", "PGPORT", "PGDATABASE"}
    }
    env.update(values)
    if name == "workspace":
        # Share the provisioned local artifact with the guarded demo launcher.
        # Only model settings are allowed in; identity and source grants stay private.
        model = dotenv_values(ROOT / ".tmp/data-workspace/chronos.env")
        for key in ("FLOWBRIDGE_WORKSPACE_CHRONOS_PATH", "FLOWBRIDGE_WORKSPACE_CHRONOS_REVISION"):
            if not env.get(key) and model.get(key):
                env[key] = model[key]
    paths = [str(ROOT / "backend"), str(ROOT)]
    if name in {"sales", "warehouse", "logistics"}:
        paths.insert(0, str(ROOT / "services" / (name + "-api")))
    elif name == "workspace":
        paths.insert(0, str(ROOT / "services/data-workspace-api"))
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def serve(name):
    if name == "portal":
        env = process_environment("core")
        # The frontend receives only public URLs and identity configuration.
        env = {k: v for k, v in env.items() if not k.startswith(("FLOWBRIDGE_", "DXOS_"))}
        env.update(
            FLOWBRIDGE_WEB_API_URL="http://127.0.0.1:18000",
            FLOWBRIDGE_WEB_ISSUER=ISSUER,
            FLOWBRIDGE_WEB_CLIENT_ID="flowbridge-web-a",
            FLOWBRIDGE_WORKSPACE_API_URL="http://127.0.0.1:18090",
        )
        return subprocess.call(
            ["node", str(ROOT / "portal/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1"],
            cwd=ROOT / "portal",
            env=env,
        )
    env = process_environment(name)
    if name == "worker":
        args = ["-m", "app.core.runtime", "worker"]
    else:
        args = [
            "-m",
            "uvicorn",
            "app.core.runtime:create_app" if name == "core" else "app.runtime:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORTS[name]),
        ]
    return subprocess.call([sys.executable, *args], cwd=DIRECTORY, env=env)


def start():
    ledger = state()
    if not ledger.get("configured"):
        raise ValueError("Configure the owned profile first")
    runtime_path = DIRECTORY / "processes.json"
    if runtime_path.exists() and json.loads(runtime_path.read_text()).get("status") == "RUNNING":
        raise ValueError("An owned runtime is already recorded; use status/stop before restart")
    for name, port in {**PORTS, "flowable": 18083}.items():
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError(
                    f"Port {port} ({name}) is occupied; no existing process was stopped"
                )
    from scripts.decision_test_environment import docker
    from scripts.decision_workflow_test_environment import IMAGE

    container_name = "flowbridge-decision-demo-flowable"
    if docker("ps", "-a", "--filter", "name=^/" + container_name + "$", "--format", "{{.ID}}"):
        raise ValueError("Existing engine container must be reconciled explicitly before startup")
    # Docker env-file syntax is not dotenv JSON quoting.
    docker_file = DIRECTORY / "flowable-docker.env"
    docker_file.write_text(
        "".join(k + "=" + v + "\n" for k, v in dotenv_values(DIRECTORY / "flowable.env").items())
    )
    container = docker(
        "run",
        "--detach",
        "--name",
        container_name,
        "--label",
        "flowbridge.decision-demo=" + ledger["org_id"],
        "--network",
        "flowbridge-local_default",
        "--env-file",
        str(docker_file),
        "--publish",
        "127.0.0.1:18083:18081",
        "--memory",
        "1536m",
        IMAGE,
    )
    runtime = {
        "status": "STARTING",
        "run_id": uuid4().hex,
        "org_id": ledger["org_id"],
        "container": container,
        "processes": {},
    }

    def record():
        runtime_path.write_text(json.dumps(runtime, indent=2), encoding="utf-8")

    record()
    logs = []
    try:
        flowable = dotenv_values(DIRECTORY / "flowable.env")
        with httpx.Client(
            timeout=3, auth=("decision-demo-admin", flowable["FLOWABLE_REST_APP_ADMIN_PASSWORD"])
        ) as http:
            for attempt in range(90):
                try:
                    if (
                        http.get(
                            "http://127.0.0.1:18083/flowable-rest/service/management/engine"
                        ).status_code
                        == 200
                    ):
                        break
                except httpx.HTTPError:
                    pass
                if attempt == 89:
                    raise RuntimeError("Owned Flowable readiness timeout")
                time.sleep(2)
        for name in ("core", "worker", "sales", "warehouse", "logistics", "workspace", "portal"):
            log = (DIRECTORY / (name + ".log")).open("wb")
            logs.append(log)
            command = [sys.executable, "-m", "scripts.decision_demo_runtime", "serve", name]
            command += ["--run-id", runtime["run_id"]]
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            runtime["processes"][name] = {
                "pid": process.pid,
                "command_marker": "scripts.decision_demo_runtime",
                "created": time.time(),
            }
            record()
            if name == "worker":
                continue
            for attempt in range(60):
                if process.poll() is not None:
                    raise RuntimeError(
                        "Owned service exited: " + name + "; inspect its private log"
                    )
                try:
                    if (
                        httpx.get(
                            f"http://127.0.0.1:{PORTS[name]}"
                            + ("/" if name == "portal" else "/openapi.json"),
                            timeout=1,
                        ).status_code
                        == 200
                    ):
                        break
                except httpx.HTTPError:
                    pass
                if attempt == 59:
                    raise RuntimeError("Owned service readiness timeout: " + name)
                time.sleep(0.3)
        runtime["status"] = "RUNNING"
        record()
        print(
            "Local demo started: http://127.0.0.1:5174/#data. "
            "Publish/grant the reviewed capabilities before acceptance."
        )
    except Exception:
        stop()
        raise
    finally:
        for log in logs:
            log.close()


def stop(service=None):
    """Stop only exact recorded process commands/container; preserve all data."""
    path = DIRECTORY / "processes.json"
    if not path.exists():
        return
    runtime = json.loads(path.read_text())
    if runtime.get("status") == "STOPPED":
        return
    if runtime["org_id"] != state()["org_id"] or not runtime.get("run_id"):
        raise ValueError(
            "Runtime ownership ledger is incomplete; refusing to stop unrelated resources"
        )
    for name, item in reversed(list(runtime["processes"].items())):
        if service is not None and name != service:
            continue
        pid = int(item["pid"])
        if os.name == "nt":
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | "
                    "Select-Object CommandLine | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            command = json.loads(result.stdout)["CommandLine"] if result.stdout.strip() else ""
        else:
            proc = Path(f"/proc/{pid}/cmdline")
            command = proc.read_text().replace("\0", " ") if proc.exists() else ""
        if not command:
            continue
        if not all(
            marker in command
            for marker in ("scripts.decision_demo_runtime", "serve", name, runtime["run_id"])
        ):
            raise ValueError("Recorded PID was reused; refusing to stop it")
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            if os.getpgid(pid) != pid:
                raise ValueError("Recorded service is outside its owned process group")
            os.killpg(pid, 15)
    if service is not None:
        runtime["processes"].pop(service, None)
        path.write_text(json.dumps(runtime, indent=2), encoding="utf-8")
        return
    from scripts.decision_test_environment import docker

    container = runtime.get("container")
    found = docker("inspect", container, check=False) if container else ""
    if found:
        detail = json.loads(found)[0]
        if (
            detail["Id"] != container
            or detail["Name"] != "/flowbridge-decision-demo-flowable"
            or detail["Config"]["Labels"].get("flowbridge.decision-demo") != runtime["org_id"]
        ):
            raise ValueError("Engine container no longer matches the owned demo")
        docker("rm", "--force", container)
    runtime["status"] = "STOPPED"
    path.write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    print("Stopped only owned demo processes/container; databases and users retained")


def restart(service):
    if service not in {*PORTS, "worker"}:
        raise ValueError("Select one recorded local demo service")
    stop(service)
    path = DIRECTORY / "processes.json"
    runtime = json.loads(path.read_text())
    if runtime["status"] != "RUNNING":
        raise ValueError("Only a running owned runtime can restart one service")
    with (DIRECTORY / (service + ".log")).open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "scripts.decision_demo_runtime",
                "serve",
                service,
                "--run-id",
                runtime["run_id"],
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    runtime["processes"][service] = {
        "pid": process.pid,
        "command_marker": "scripts.decision_demo_runtime",
        "created": time.time(),
    }
    path.write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    if service != "worker":
        for attempt in range(60):
            if process.poll() is not None:
                raise RuntimeError("Restarted service exited; inspect private log")
            try:
                url = f"http://127.0.0.1:{PORTS[service]}" + (
                    "/" if service == "portal" else "/openapi.json"
                )
                if httpx.get(url, timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if attempt == 59:
                raise RuntimeError("Restarted service readiness timeout")
            time.sleep(0.3)
    print("Restarted only owned demo service: " + service)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("configure", "start", "stop", "restart", "serve", "status")
    )
    parser.add_argument("service", nargs="?", choices=(*PORTS, "worker"))
    parser.add_argument("--run-id")
    parser.add_argument("--coordinator")
    args = parser.parse_args()
    if args.operation == "configure":
        configure(args.coordinator)
    elif args.operation == "start":
        start()
    elif args.operation == "stop":
        stop()
    elif args.operation == "restart":
        restart(args.service)
    elif args.operation == "serve":
        if not args.service:
            parser.error("A service name is required")
        raise SystemExit(serve(args.service))
    else:
        file = DIRECTORY / "processes.json"
        print(file.read_text() if file.exists() else "No owned runtime recorded")


if __name__ == "__main__":
    main()
