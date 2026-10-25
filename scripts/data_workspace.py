# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit local Layer 2 setup/start. Does not change business/Core/engine schemas."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".tmp/data-workspace"


def migrate():
    config = Config()
    config.set_main_option("script_location", str(ROOT / "services/data-workspace-api/migrations"))
    command.upgrade(config, "head")


def initialize(org_id):
    from uuid import UUID

    org_id = str(UUID(org_id))
    values = dotenv_values(ROOT / ".tmp/local-postgres/host.env")
    admin = make_url(values["DXOS_ADMIN_URL"]).set(drivername="postgresql")
    if admin.host != "127.0.0.1" or admin.database != "postgres":
        raise ValueError("Only the existing local PostgreSQL admin target is supported")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    file = RUNTIME / "runtime.env"
    if file.exists():
        settings = dotenv_values(file)
        if settings["FLOWBRIDGE_WORKSPACE_ORG_ID"] != org_id:
            raise ValueError("Existing workspace org binding differs; no changes made")
    else:
        password = secrets.token_hex(24)
        with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname='workspace_owner'").fetchone():
                raise ValueError(
                    "Existing workspace role without local config; refusing credential changes"
                )
            if conn.execute(
                "SELECT 1 FROM pg_database WHERE datname='flowbridge_workspace_db'"
            ).fetchone():
                raise ValueError("Existing database without config; refusing to adopt")
            conn.execute(
                sql.SQL(
                    "CREATE ROLE workspace_owner LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Literal(password))
            )
            conn.execute("CREATE DATABASE flowbridge_workspace_db OWNER workspace_owner")
            conn.execute("REVOKE CONNECT ON DATABASE flowbridge_workspace_db FROM PUBLIC")
            conn.execute("GRANT CONNECT ON DATABASE flowbridge_workspace_db TO workspace_owner")
        url = URL.create(
            "postgresql+psycopg",
            username="workspace_owner",
            password=password,
            host=admin.host,
            port=admin.port,
            database="flowbridge_workspace_db",
        )
        settings = {
            "FLOWBRIDGE_WORKSPACE_DATABASE_URL": url.render_as_string(hide_password=False),
            "FLOWBRIDGE_WORKSPACE_CORE_URL": "http://127.0.0.1:18000",
            "FLOWBRIDGE_WORKSPACE_ORG_ID": org_id,
        }
        file.write_text("".join(f"{k}='{v}'\n" for k, v in settings.items()), encoding="utf-8")
    os.environ.update(settings)
    migrate()
    print(
        "Workspace database ready at workspace_0001; config retained in ignored .tmp/data-workspace"
    )


def demo_access(*, revoke=False):
    """Existing local fixture accounts only; all grant decisions remain Core API commands."""
    from scripts.integration_utils import tokens

    auth = tokens()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    file = RUNTIME / "demo-access.json"
    state = json.loads(file.read_text()) if file.exists() else {}
    if (
        not state
        or (not revoke and state.get("revoked"))
        or (
            not revoke
            and datetime.fromisoformat(state["expires_at"].replace("Z", "+00:00"))
            <= datetime.now(UTC)
        )
    ):
        state = {
            "run_id": uuid4().hex,
            "grants": [],
            "expires_at": (datetime.now(UTC) + timedelta(hours=24))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        file.write_text(json.dumps(state, indent=2))
    with httpx.Client(
        base_url="http://127.0.0.1:18000/api/v1/core",
        timeout=60,
        headers={"Authorization": "Bearer " + auth["it"]},
    ) as client:

        def call(method, path, expected=200, body=None, key=None, consumer=False):
            headers = {"Authorization": "Bearer " + auth["sales"]} if consumer else {}
            if key:
                headers["Idempotency-Key"] = key
            response = client.request(method, path, json=body, headers=headers)
            if response.status_code != expected:
                raise RuntimeError(
                    f"Core demo access failed: HTTP {response.status_code} at {path}"
                )
            return response.json()

        def wait(receipt):
            job = receipt.get("job", receipt)
            for _ in range(180):
                result = call("GET", "/publication-jobs/" + job["job_id"])
                if result["status"] == "SUCCEEDED":
                    return
                if result["status"] in {"FAILED", "CANCELLED"}:
                    raise RuntimeError("Core grant worker rejected demo command")
                time.sleep(0.2)
            raise RuntimeError("Core grant worker timeout")

        if revoke:
            for identifier in state["grants"]:
                grant = call("GET", "/access-grants/" + identifier)
                if grant["status"] in {"REVOKED", "EXPIRED", "REVOKING"}:
                    continue
                request = call("GET", "/access-requests/" + grant["access_request_id"])
                if request["reason"] != "Data Workspace local demo (24h)":
                    raise RuntimeError("Refusing to revoke a grant outside this demo")
                wait(
                    call(
                        "POST",
                        "/access-grants/" + identifier + "/revoke",
                        202,
                        body={
                            "expected_desired_revision": grant["desired_revision"],
                            "reason": "End local Workspace demo",
                        },
                        key="workspace-demo-revoke-" + identifier,
                    )
                )
            print("Revoked only the local demo grants recorded by this command")
            state["revoked"] = True
            file.write_text(json.dumps(state, indent=2))
            return
        for identifier in state["grants"]:
            if call("GET", "/access-grants/" + identifier)["status"] in {"REVOKED", "EXPIRED"}:
                raise RuntimeError("Demo access changed; run revoke-demo then grant-demo to reset")
        me = call("GET", "/me", consumer=True)
        department = next(m["department_id"] for m in me["memberships"] if m["status"] == "ACTIVE")
        ledgers = list(
            (ROOT / ".tmp/docker-integration/runs").glob("local-postgres-*/resources.json")
        )
        if not ledgers:
            raise ValueError("Existing local publication ledger is required")
        ledger = json.loads(max(ledgers, key=lambda p: p.stat().st_mtime).read_text())
        authorized = {
            p["id"]: p for p in call("GET", "/authorized-data-products", consumer=True)["items"]
        }
        for imported in ledger["imports"].values():
            for identifier in imported["data_product_ids"]:
                product = call("GET", "/data-products/" + identifier)
                contract = call("GET", "/data-contracts/" + product["active_contract_id"])
                columns = sorted(f["column"] for f in contract["contract_json"]["fields"])
                existing = authorized.get(identifier)
                if existing and {f["column"] for f in existing["fields"]} >= set(columns):
                    continue  # Never duplicate or take ownership of pre-existing grants.
                scope = {"action": "SELECT", "columns": columns, "row_scope": {"kind": "ALL"}}
                key = state["run_id"] + "-" + identifier
                request = call(
                    "POST",
                    "/access-requests",
                    201,
                    body={
                        "data_product_id": identifier,
                        "contract_version_id": contract["id"],
                        "consumer_department_id": department,
                        "consumer_principal_id": None,
                        "requested_scope_json": scope,
                        "reason": "Data Workspace local demo (24h)",
                    },
                    key="workspace-demo-request-" + key,
                )
                approved = call(
                    "POST",
                    "/access-requests/" + request["id"] + "/approve",
                    202,
                    body={
                        "expected_revision": request["revision"],
                        "scope_json": scope,
                        "expires_at": state["expires_at"],
                    },
                    key="workspace-demo-approve-" + key,
                )
                if approved["grant"]["id"] not in state["grants"]:
                    state["grants"].append(approved["grant"]["id"])
                    file.write_text(json.dumps(state, indent=2))
                wait(approved)
        print("Local read-only demo access ready; expiry:", state["expires_at"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=[
            "init-local",
            "migrate",
            "serve",
            "export-openapi",
            "prepare-local-runtime",
            "serve-core",
            "serve-worker",
            "serve-portal",
            "grant-demo",
            "revoke-demo",
        ],
    )
    parser.add_argument("--org-id")
    args = parser.parse_args()
    if args.operation in {"grant-demo", "revoke-demo"}:
        demo_access(revoke=args.operation == "revoke-demo")
        return
    if args.operation == "prepare-local-runtime":
        from scripts.integration_utils import write_env

        identity = {
            k: v
            for k, v in dotenv_values(ROOT / ".tmp/docker-integration/core.env").items()
            if k.startswith("FLOWBRIDGE_OIDC_")
        }
        local = dotenv_values(ROOT / ".tmp/local-postgres/host.env")
        if not identity or not local:
            raise ValueError("Existing local Core/SSO configuration required")
        # This dedicated local Core composition serves the existing PKCE frontend client.
        # Preserve issuer/audience verification, service clients and all product grants.
        allowed = json.loads(identity.get("FLOWBRIDGE_OIDC_ALLOWED_CLIENTS", "[]"))
        browser_client = os.environ.get("FLOWBRIDGE_WEB_CLIENT_ID", "flowbridge-web-a")
        if allowed and browser_client not in allowed:
            identity["FLOWBRIDGE_OIDC_ALLOWED_CLIENTS"] = json.dumps([*allowed, browser_client])
        RUNTIME.mkdir(parents=True, exist_ok=True)
        write_env(RUNTIME / "core.env", {**identity, **local})
        print("Prepared ignored Core process config from existing local identity/database settings")
        return
    if args.operation == "init-local":
        if not args.org_id:
            parser.error("--org-id is required (the actual single-enterprise business org)")
        initialize(args.org_id)
        return
    local = RUNTIME / "runtime.env"
    workspace_config = dict(dotenv_values(local)) if local.exists() else {}
    model_config = RUNTIME / "chronos.env"
    if model_config.exists():
        workspace_config.update(dotenv_values(model_config))
    if args.operation in {"migrate", "serve"}:
        # Explicit process environment wins over saved local configuration.
        for key, value in workspace_config.items():
            os.environ.setdefault(key, value)
    if args.operation == "migrate":
        migrate()
    elif args.operation in {"serve-core", "serve-worker", "serve-portal"}:
        core_config = dict(dotenv_values(RUNTIME / "core.env"))
        if args.operation == "serve-portal":
            # Vite needs public identity/API URLs, never saved database or Core secrets.
            env = dict(os.environ)
            env.setdefault(
                "FLOWBRIDGE_WEB_API_URL", workspace_config["FLOWBRIDGE_WORKSPACE_CORE_URL"]
            )
            env.setdefault("FLOWBRIDGE_WEB_ISSUER", core_config["FLOWBRIDGE_OIDC_ISSUER"])
            env.setdefault("FLOWBRIDGE_WEB_CLIENT_ID", "flowbridge-web-a")
            subprocess.run(
                [
                    "node",
                    str(ROOT / "portal/node_modules/vite/bin/vite.js"),
                    "--host",
                    "127.0.0.1",
                ],
                cwd=ROOT / "portal",
                env=env,
                check=True,
            )
        else:
            env = {**core_config, **os.environ}
            args = (
                ["-m", "app.core.runtime", "worker"]
                if args.operation == "serve-worker"
                else [
                    "-m",
                    "uvicorn",
                    "app.core.runtime:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18000",
                ]
            )
            subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True)
    elif args.operation == "serve":
        subprocess.run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.runtime:create_app",
                "--factory",
                "--app-dir",
                str(ROOT / "services/data-workspace-api"),
                "--host",
                "127.0.0.1",
                "--port",
                "18090",
            ],
            cwd=ROOT,
            check=True,
        )
    else:
        sys.path.insert(0, str(ROOT / "services/data-workspace-api"))
        # These placeholders construct metadata only; no connection/model operations occur.
        from app.runtime import create_app

        schema = create_app(connector=object(), repository=object()).openapi()
        (ROOT / "contracts/data-workspace.openapi.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Exported Data Workspace API contract")


if __name__ == "__main__":
    main()
