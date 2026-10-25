# SPDX-License-Identifier: GPL-3.0-or-later
"""Disposable Flowable + PostgreSQL, isolated from deployed service/database configs."""

import json
import secrets
import subprocess
import time
from contextlib import contextmanager

import httpx
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from scripts.decision_test_environment import LABEL, ROOT, disposable_workspace, docker

IMAGE = (
    "flowable/flowable-rest:7.2.0@sha256:"
    "bcb895b12a95c7a11c07daef9a66c7c7e021038963b0877ee1f01d43059694ee"
)


@contextmanager
def disposable_workflow():
    with disposable_workspace() as (env, directory):
        run_id = env["FLOWBRIDGE_DECISION_TEST_RUN"]
        parent = json.loads((directory / "resources.json").read_text())
        network_name = f"decision-workflow-{run_id}"
        container_name = f"decision-flowable-{run_id}"
        ledger = {"run_id": run_id, "status": "STARTING", "container": None, "network": None}
        env_file = directory / "flowable.env"
        password, rest_password = secrets.token_hex(24), secrets.token_hex(24)
        admin = make_url(env["DXOS_TEST_ADMIN_URL"]).set(drivername="postgresql")
        with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "CREATE ROLE decision_flowable_owner LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Literal(password))
            )
            conn.execute("CREATE DATABASE decision_flowable_db OWNER decision_flowable_owner")
            conn.execute("REVOKE CONNECT ON DATABASE decision_flowable_db FROM PUBLIC")
            conn.execute(
                "GRANT CONNECT ON DATABASE decision_flowable_db TO decision_flowable_owner"
            )
        values = {
            "SPRING_DATASOURCE_URL": "jdbc:postgresql://decision-postgres:5432/decision_flowable_db",
            "SPRING_DATASOURCE_USERNAME": "decision_flowable_owner",
            "SPRING_DATASOURCE_PASSWORD": password,
            "SERVER_PORT": "18081",
            "FLOWABLE_REST_APP_ADMIN_USER_ID": "decision-test-admin",
            "FLOWABLE_REST_APP_ADMIN_PASSWORD": rest_password,
            "FLOWABLE_DATABASE_SCHEMA_UPDATE": "true",
            "FLOWABLE_REST_APP_CREATE_DEMO_DEFINITIONS": "false",
            "REST_VARIABLES_ALLOW_SERIALIZABLE": "false",
            "FLOWABLE_REST_APP_SWAGGER_DOCS_ENABLED": "false",
            "SPRING_DATASOURCE_HIKARI_MAXIMUM_POOL_SIZE": "4",
            "SPRING_DATASOURCE_HIKARI_MINIMUM_IDLE": "1",
        }
        env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))

        def record():
            (directory / "workflow-resources.json").write_text(json.dumps(ledger, indent=2))

        def owned(kind, identifier, name):
            item = json.loads(
                docker(kind, "inspect", identifier)
                if kind == "network"
                else docker("inspect", identifier)
            )[0]
            labels = (
                item.get("Labels", {}) if kind == "network" else item["Config"].get("Labels", {})
            )
            if (
                item["Id"] != identifier
                or labels.get(LABEL) != run_id
                or item["Name"].lstrip("/") != name
            ):
                raise RuntimeError("Refusing operation on a foreign Workflow test resource")

        try:
            network = docker("network", "create", "--label", f"{LABEL}={run_id}", network_name)
            ledger["network"] = network
            record()
            docker(
                "network", "connect", "--alias", "decision-postgres", network, parent["container"]
            )
            container = docker(
                "run",
                "--detach",
                "--name",
                container_name,
                "--label",
                f"{LABEL}={run_id}",
                "--network",
                network,
                "--env-file",
                str(env_file),
                "--publish",
                "127.0.0.1::18081",
                "--memory",
                "1536m",
                IMAGE,
            )
            ledger["container"] = container
            record()
            port = int(docker("port", container, "18081/tcp").rsplit(":", 1)[1])
            base = f"http://127.0.0.1:{port}/flowable-rest/service"
            with httpx.Client(timeout=3, auth=("decision-test-admin", rest_password)) as http:
                for attempt in range(90):
                    try:
                        response = http.get(base + "/management/engine")
                        if (
                            response.status_code == 200
                            and response.json().get("version") == "7.2.0"
                        ):
                            break
                    except (httpx.HTTPError, ValueError):
                        pass
                    if attempt == 89:
                        raise RuntimeError(
                            "Isolated Flowable readiness timed out; see owned container"
                        )
                    time.sleep(2)
            ledger.update(status="READY", port=port)
            record()
            env.update(
                FLOWBRIDGE_DECISION_FLOWABLE_TEST_URL=base,
                FLOWBRIDGE_DECISION_FLOWABLE_TEST_USER="decision-test-admin",
                FLOWBRIDGE_DECISION_FLOWABLE_TEST_PASSWORD=rest_password,
                FLOWBRIDGE_DECISION_FLOWABLE_TEST_CONTAINER=container,
            )
            yield env, directory
        finally:
            if ledger["container"]:
                owned("container", ledger["container"], container_name)
                docker("rm", "--force", ledger["container"])
            if ledger["network"]:
                owned("network", ledger["network"], network_name)
                docker("network", "disconnect", ledger["network"], parent["container"])
                docker("network", "rm", ledger["network"])
            env_file.unlink(missing_ok=True)
            ledger["status"] = "CLEANED"
            record()


def main():
    import sys

    with disposable_workflow() as (env, directory):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/e2e/test_workspace_persistence.py",
                "tests/e2e/test_decision_workflow.py",
                "-m",
                "integration",
                "-q",
                "--tb=short",
                "--junitxml",
                str(directory / "workflow-results.xml"),
            ],
            cwd=ROOT,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        (directory / "workflow-test.log").write_text(result.stdout, encoding="utf-8")
        print(result.stdout[-8000:])
        print(f"Workflow integration evidence: {directory}")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
