# SPDX-License-Identifier: GPL-3.0-or-later
"""Disposable loopback-only PostgreSQL for Workspace integration; never reads .env."""

import argparse
import json
import os
import secrets
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
LABEL = "flowbridge.decision-test-run"


def validate_test_url(value):
    url = make_url(value)
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not url.port
        or url.database != "flowbridge_workspace_db"
        or url.username != "workspace_owner"
        or url.drivername != "postgresql+psycopg"
        or url.query
    ):
        raise ValueError("Only explicit disposable loopback Workspace test targets are allowed")
    return url


def child_environment(url, run_id):
    validate_test_url(url)
    # Do not inherit deployed database targets or old live E2E publication ledgers.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("FLOWBRIDGE_", "DXOS_"))
        and key not in {"DATABASE_URL", "PGPASSWORD", "PGHOST", "PGPORT", "PGDATABASE"}
    }
    env.update(
        FLOWBRIDGE_WORKSPACE_DATABASE_URL=url,
        FLOWBRIDGE_WORKSPACE_TEST_DATABASE_URL=url,
        FLOWBRIDGE_DECISION_TEST_RUN=run_id,
    )
    return env


def docker(*args, check=True):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=180
    )
    if check and result.returncode:
        # Arguments and raw engine errors can include paths or environment values.
        raise RuntimeError(f"Docker {args[0]} failed; check the local Docker engine")
    return result.stdout.strip()


def owned_container(identifier, run_id):
    found = json.loads(docker("inspect", identifier))[0]
    if (
        found["Id"] != identifier
        or found["Config"].get("Labels", {}).get(LABEL) != run_id
        or found["Name"] != f"/flowbridge-decision-test-{run_id}"
    ):
        raise RuntimeError("Refusing cleanup of a container outside this test run")


@contextmanager
def disposable_workspace(image="postgres:16"):
    if image not in {"postgres:16", "postgres:17"}:
        raise ValueError("Use the reviewed PostgreSQL 16/17 test images")
    run_id = uuid4().hex
    directory = ROOT / ".tmp" / "decision-tests" / run_id
    directory.mkdir(parents=True)
    credentials = directory / "postgres.env"
    password = secrets.token_hex(24)
    credentials.write_text(
        f"POSTGRES_DB=postgres\nPOSTGRES_USER=decision_test_admin\nPOSTGRES_PASSWORD={password}\n",
        encoding="utf-8",
    )
    identifier = None
    ledger = {"run_id": run_id, "image": image, "status": "STARTING", "container": None}

    def record():
        (directory / "resources.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    record()
    try:
        docker("info", "--format", "{{.ServerVersion}}")
        identifier = docker(
            "run",
            "--detach",
            "--rm",
            "--name",
            f"flowbridge-decision-test-{run_id}",
            "--label",
            f"{LABEL}={run_id}",
            "--env-file",
            str(credentials),
            "--publish",
            "127.0.0.1::5432",
            image,
        )
        ledger["container"] = identifier
        record()
        import psycopg

        port = int(docker("port", identifier, "5432/tcp").rsplit(":", 1)[1])
        admin_url = make_url("postgresql+psycopg://decision_test_admin@127.0.0.1/postgres")
        admin_url = admin_url.set(port=port, password=password)
        for attempt in range(40):
            try:
                with psycopg.connect(
                    admin_url.set(drivername="postgresql").render_as_string(hide_password=False),
                    connect_timeout=2,
                ) as conn:
                    assert conn.execute("SELECT current_database(), current_user").fetchone() == (
                        "postgres",
                        "decision_test_admin",
                    )
                break
            except psycopg.OperationalError:
                if attempt == 39:
                    raise RuntimeError("Disposable PostgreSQL readiness timed out") from None
                time.sleep(0.5)
        from psycopg import sql

        with psycopg.connect(
            admin_url.set(drivername="postgresql").render_as_string(hide_password=False),
            autocommit=True,
        ) as conn:
            conn.execute(
                sql.SQL(
                    "CREATE ROLE workspace_owner LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Literal(password))
            )
            conn.execute("CREATE DATABASE flowbridge_workspace_db OWNER workspace_owner")
            conn.execute("REVOKE CONNECT ON DATABASE flowbridge_workspace_db FROM PUBLIC")
            conn.execute("GRANT CONNECT ON DATABASE flowbridge_workspace_db TO workspace_owner")
        url = admin_url.set(username="workspace_owner", database="flowbridge_workspace_db")
        url = url.render_as_string(hide_password=False)
        validate_test_url(url)
        ledger.update(status="READY", port=port)
        record()
        yield child_environment(url, run_id), directory
    finally:
        if identifier:
            owned_container(identifier, run_id)
            docker("rm", "--force", identifier)
        credentials.unlink(missing_ok=True)
        ledger["status"] = "CLEANED"
        record()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="postgres:16", choices=["postgres:16", "postgres:17"])
    args = parser.parse_args()
    with disposable_workspace(args.image) as (env, directory):
        import sys

        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests/e2e/test_workspace_persistence.py",
            "-m",
            "integration",
            "-q",
            "--tb=short",
            "--junitxml",
            str(directory / "results.xml"),
        ]
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        print(f"Workspace integration evidence: {directory}")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
