"""Real read-only workflow: no mocked IdP, engine, callback, metadata or data access."""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from dotenv import dotenv_values
from pydantic import SecretStr
from scripts.docker_env import write_env
from scripts.e2e_environment import ROOT, RUNTIME, business_snapshot, engine_for
from scripts.e2e_runtime import NativeRuntime
from sqlalchemy import text
from sqlalchemy.engine import make_url
from tests.core.persistence.test_onboarding import submission
from tests.e2e.test_automated_onboarding import call, import_manifest, wait_job
from tests.e2e.workflow_fixture import prepare_identities, workflow_tokens

from app.core.applications.manifest import source_name
from app.core.data_products.credentials import HasuraJwtCredentials
from app.core.data_products.querying import QuerySelection
from app.core.data_products.reading import TrustedHasuraContext

pytestmark = pytest.mark.e2e


def test_real_governed_data_actions_custom_http_user_task_and_durable_events():
    if os.environ.get("FLOWBRIDGE_WORKFLOW_DATA_E2E_ENABLED") != "1":
        pytest.skip("Explicit opt-in: FLOWBRIDGE_WORKFLOW_DATA_E2E_ENABLED=1")
    identity = prepare_identities()
    credentials = workflow_tokens(identity)
    flowable_path = ROOT / ".tmp/flowable-integration/docker.env"
    flowable = dotenv_values(flowable_path)
    run_id = "workflow-data-" + uuid4().hex[:12]
    directory = RUNTIME / "runs" / run_id
    directory.mkdir()
    (directory / "resources.json").write_text(json.dumps({"run_id": run_id, "evidence": {}}))
    root = dotenv_values(ROOT / ".env")
    urls = {
        name: make_url(root["DXOS_" + name.upper() + "_URL"])
        for name in ("warehouse", "sales", "logistics")
    }
    baseline = {name: business_snapshot(url) for name, url in urls.items()}
    reader = dotenv_values(RUNTIME / "hasura.env")["FB_E2E_WAREHOUSE_URL"]
    source_url = make_url(reader).set(drivername="postgresql+psycopg")
    engine = engine_for(source_url)
    try:
        with engine.connect().execution_options(postgresql_readonly=True) as connection:
            assert connection.connection.driver_connection.pgconn.ssl_in_use
            row = dict(
                connection.execute(
                    text("SELECT id,sku,name,uom FROM public.products ORDER BY id LIMIT 1")
                )
                .mappings()
                .one()
            )
            row["id"] = str(row["id"])
    finally:
        engine.dispose()
    key = "wf_data_" + uuid4().hex[:12]
    event_key = "wf_event_" + uuid4().hex[:12]
    app_key = "wf_" + uuid4().hex[:12]
    action_id = "warehouse.inspect_" + uuid4().hex[:12]
    webhook_secret = secrets.token_urlsafe(36)
    runtime_secrets = json.loads((RUNTIME / "secrets.json").read_text())
    context = TrustedHasuraContext(
        UUID(identity["principals"]["sales"]),
        UUID(identity["departments"]["sales"]["id"]),
        uuid4(),
        uuid4(),
        uuid4(),
        1,
        1,
        datetime.now(UTC),
        None,
    )
    with httpx.Client(timeout=20) as graphql:
        for signing_key, expected in (
            (runtime_secrets["signing_key"], True),
            ("wrong-key-" * 5, False),
        ):
            token = asyncio.run(
                HasuraJwtCredentials(
                    SecretStr(signing_key), "flowbridge-core", "flowbridge-data"
                ).issue(context)
            ).get_secret_value()
            result = graphql.post(
                "http://127.0.0.1:18080/v1/graphql",
                headers={"Authorization": "Bearer " + token},
                json={"query": "{__typename}"},
            ).json()
            assert (result.get("data") == {"__typename": "query_root"}) == expected
    flowable["FLOWBRIDGE_WORKFLOW_ENGINE_TOKEN"] = credentials["sales"]
    write_env(flowable_path, flowable)
    config = {
        **dotenv_values(RUNTIME / "core.env"),
        **flowable,
        "FLOWBRIDGE_WORKFLOW_ENABLED": "true",
        "FLOWBRIDGE_CORE_VERIFIED_HASURA_AUTH_PROFILE": "true",
        "FLOWBRIDGE_FLOWABLE_TIMEOUT_SECONDS": "90",
        "FLOWBRIDGE_WORKFLOW_ACTION_TIMEOUT_SECONDS": "60",
        "FLOWBRIDGE_WORKFLOW_ALLOWED_WORKFLOWS": json.dumps([key, event_key]),
        "FLOWBRIDGE_WORKFLOW_SERVICE_BASES": json.dumps({"core": "http://127.0.0.1:18000"}),
        "FLOWBRIDGE_WORKFLOW_ALLOWED_HTTP_HOSTS": '["127.0.0.1"]',
        "FLOWBRIDGE_WORKFLOW_EVENT_WEBHOOK_SECRET": webhook_secret,
        "FLOWBRIDGE_WORKFLOW_EVENT_SUBSCRIPTIONS": json.dumps(
            [
                {
                    "subscription_id": run_id + ".start",
                    "event_type": "workflow.probe.start",
                    "workflow_key": event_key,
                    "mode": "START",
                },
                {
                    "subscription_id": run_id + ".resume",
                    "event_type": "workflow.probe.resume",
                    "workflow_key": event_key,
                    "mode": "CORRELATE",
                    "message_name": "flowbridge.probe.done",
                },
                {
                    "subscription_id": run_id + ".review",
                    "event_type": "workflow.review.done",
                    "workflow_key": key,
                    "mode": "CORRELATE",
                    "message_name": "flowbridge.review.done",
                },
            ]
        ),
        "FLOWBRIDGE_HASURA_ALLOWED_DATABASE_HOSTS": json.dumps([source_url.host]),
        "FLOWBRIDGE_HASURA_ALLOWED_SOURCES": json.dumps([source_name(app_key, "pg")]),
        "FLOWBRIDGE_HASURA_SECRET_BINDINGS": '{"workflow-reader":"FB_E2E_WAREHOUSE_URL"}',
        "FLOWBRIDGE_CORE_SOURCE_SCOPES": json.dumps(
            {"workflow-reader": [{"schema": "public", "name": "products"}]}
        ),
        "FLOWBRIDGE_CORE_WORKER_POLL_SECONDS": "0.2",
        "FB_E2E_WAREHOUSE_URL": reader,
    }
    write_env(directory / "core.env", config)
    runtime = NativeRuntime(directory, api_host="0.0.0.0")
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(flowable_path),
        "-f",
        str(ROOT / "infra/flowable/docker-compose.flowable.yml"),
    ]

    def docker(*arguments):
        subprocess.run(
            [*compose, *arguments],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def ready():
        deadline = time.monotonic() + 150
        with httpx.Client(timeout=5) as client:
            while time.monotonic() < deadline:
                try:
                    result = client.get(
                        flowable["FLOWBRIDGE_FLOWABLE_BASE_URL"] + "/management/engine",
                        auth=(
                            flowable["FLOWBRIDGE_FLOWABLE_USERNAME"],
                            flowable["FLOWBRIDGE_FLOWABLE_PASSWORD"],
                        ),
                    )
                    if result.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(2)
        pytest.fail("Flowable is not ready")

    try:
        docker("up", "-d", "--force-recreate", "flowable")
        ready()
        runtime.start("api")
        engine = engine_for(make_url(root["DXOS_CORE_URL"]))
        try:
            with engine.connect().execution_options(postgresql_readonly=True) as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM publication_jobs "
                            "WHERE status IN ('QUEUED','RETRY','RUNNING') "
                            "AND requested_by <> :fixture_admin"
                        ),
                        {"fixture_admin": identity["principals"]["it"]},
                    )
                    == 0
                )
        finally:
            engine.dispose()
        runtime.start("worker")
        with (
            httpx.Client(
                base_url=runtime.base_url,
                headers={"Authorization": "Bearer " + credentials["it"]},
                timeout=120,
            ) as it,
            httpx.Client(
                base_url=runtime.base_url,
                headers={"Authorization": "Bearer " + credentials["sales"]},
                timeout=120,
            ) as sales,
        ):
            body = submission(
                source_url,
                identity["departments"]["warehouse"]["code"],
                app_key,
                object_name="products",
                secret_ref="workflow-reader",
                columns=[
                    {
                        "column": name,
                        "name": name,
                        "physical_type": physical,
                        "wire_type": "string",
                        "nullable": False,
                        "description": None,
                    }
                    for name, physical in (
                        ("id", "UUID"),
                        ("sku", "VARCHAR(80)"),
                        ("name", "VARCHAR(255)"),
                        ("uom", "VARCHAR(30)"),
                    )
                ],
            )
            imported = import_manifest(it, body)
            runtime.resources["imports"] = {"warehouse": imported}
            runtime.save()
            application_id = imported["application_id"]
            source_id = imported["data_source_ids"][0]
            product_id = imported["data_product_ids"][0]
            contract_id = imported["contract_version_ids"][0]
            call(
                it,
                "PATCH",
                "/applications/" + application_id,
                json={"expected_revision": 1, "status": "ACTIVE"},
            )
            wait_job(
                it,
                call(
                    it,
                    "POST",
                    "/data-sources/" + source_id + "/connect",
                    202,
                    json={"expected_revision": 1},
                ),
            )
            call(
                it,
                "POST",
                "/data-contracts/" + contract_id + "/publish",
                json={"expected_revision": 1},
            )
            wait_job(
                it,
                call(
                    it,
                    "POST",
                    "/data-products/" + product_id + "/publish",
                    202,
                    json={"expected_revision": 1, "contract_version_id": contract_id},
                ),
            )
            product = call(it, "GET", "/data-products/" + product_id)
            scope = {"action": "SELECT", "columns": list(row), "row_scope": {"kind": "ALL"}}
            access = call(
                it,
                "POST",
                "/access-requests",
                201,
                json={
                    "data_product_id": product_id,
                    "contract_version_id": contract_id,
                    "consumer_department_id": identity["departments"]["sales"]["id"],
                    "consumer_principal_id": None,
                    "requested_scope_json": scope,
                    "reason": "Workflow acceptance " + run_id,
                },
            )
            granted = call(
                it,
                "POST",
                "/access-requests/" + access["id"] + "/approve",
                202,
                json={"expected_revision": 1, "scope_json": scope, "expires_at": None},
            )
            wait_job(it, granted["job"])
            runtime.evidence(
                "manifest_publication_grant",
                {"product_id": product_id, "grant_id": granted["grant"]["id"]},
            )
            data_request = {
                "product_key": product["product_key"],
                "contract_version": "1.0.0",
                "filters": {"id": row["id"]},
                "columns": list(row),
            }
            selection = {
                "select": list(row),
                "contract_version_id": contract_id,
                "where": {"id": {"_eq": row["id"]}},
                "limit": 1,
            }
            api = "/api/v1/workflows"
            response = sales.post(api + "/actions/data.get_record/execute", json=data_request)
            assert response.status_code == 200
            assert response.json()["result"] == row
            response = sales.post(api + "/actions/data.query/execute", json=data_request)
            assert response.status_code == 200
            assert response.json()["result"]["rows"] == [row]
            response = sales.post(
                api + "/actions/data.update/execute", json={**data_request, "patch": {}}
            )
            assert response.status_code == 409
            action = {
                "action_id": action_id,
                "version": 1,
                "owner_application": app_key,
                "implementation_type": "HTTP",
                "timeout_seconds": 60,
                "service_ref": "core",
                "method": "POST",
                "path": "/api/v1/core/data-products/" + product_id + "/query",
                "input_schema": QuerySelection.model_json_schema(),
                "output_schema": {"type": "object", "required": ["rows", "data_product_id"]},
            }
            response = it.post(
                api + "/actions", headers={"Idempotency-Key": run_id + ".action"}, json=action
            )
            assert response.status_code == 201
            bpmn = (
                (ROOT / "tests/fixtures/workflow/data-sharing.bpmn20.xml")
                .read_text()
                .replace('id="flowbridge_data_probe"', 'id="' + key + '"')
                .replace("warehouse.inspect_product", action_id)
            )
            event_bpmn = (
                (ROOT / "tests/fixtures/workflow/runtime-probe.bpmn20.xml")
                .read_text()
                .replace('id="flowbridge_runtime_probe"', 'id="' + event_key + '"')
                .replace("${assignee}", identity["users"]["sales"]["id"])
            )
            for workflow_key, xml in ((key, bpmn), (event_key, event_bpmn)):
                response = it.post(
                    api + "/definitions", json={"name": workflow_key, "bpmn_xml": xml}
                )
                assert response.status_code == 201
            start_body = {
                "business_key": run_id,
                "variables": {"data_request": data_request, "custom_request": selection},
            }
            start_headers = {"Idempotency-Key": run_id + ".run"}
            response = sales.post(
                api + "/" + key + "/start", headers=start_headers, json=start_body
            )
            assert response.status_code == 201, "Real data workflow failed to start"
            instance_id = response.json()["instance_id"]
            response = sales.post(
                api + "/" + key + "/start", headers=start_headers, json=start_body
            )
            assert response.status_code == 201 and response.json()["instance_id"] == instance_id
            response = sales.get(api + "/instances/" + instance_id)
            assert response.status_code == 200 and response.json()["variables"] == {}
            tasks = sales.get(api + "/tasks", params={"process_instance_id": instance_id}).json()[
                "tasks"
            ]
            assert len(tasks) == 1
            runtime.evidence(
                "core_flowable_gateway_hasura",
                {"instance_id": instance_id, "task_id": tasks[0]["id"]},
            )

            def event(event_id, event_type, business_key, *, expected=202):
                raw = json.dumps(
                    {
                        "event_id": event_id,
                        "event_type": event_type,
                        "payload": {"business_key": business_key},
                    }
                ).encode()
                signature = hmac.new(webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
                response = sales.post(
                    api + "/events",
                    content=raw,
                    headers={"Content-Type": "application/json", "X-Event-Signature": signature},
                )
                assert response.status_code == expected
                return response.json()

            raw = b'{"event_id":"unsigned","event_type":"none","payload":{}}'
            assert (
                sales.post(
                    api + "/events", content=raw, headers={"Content-Type": "application/json"}
                ).status_code
                == 401
            )
            started = event(run_id + ".start", "workflow.probe.start", run_id + ".event")
            event_instance_id = started["outcomes"][0]["instance_id"]
            runtime.restart("api")
            docker("restart", "flowable")
            ready()
            replay = event(run_id + ".start", "workflow.probe.start", run_id + ".event")
            assert replay["outcomes"][0]["instance_id"] == event_instance_id
            assert replay["outcomes"][0]["deduped"] is True
            event(run_id + ".start", "workflow.probe.start", "changed-payload", expected=409)
            response = sales.post(api + "/actions/" + action_id + "/execute", json=selection)
            assert response.status_code == 200 and response.json()["result"]["rows"] == [row]
            runtime.evidence("registry_and_inbox_after_restart", True)
            for identifier, event_type, business_key in (
                (instance_id, "workflow.review.done", run_id),
                (event_instance_id, "workflow.probe.resume", run_id + ".event"),
            ):
                own = sales.get(api + "/tasks", params={"process_instance_id": identifier}).json()[
                    "tasks"
                ]
                assert len(own) == 1
                assert (
                    it.post(
                        api + "/tasks/" + own[0]["id"] + "/complete", json={"variables": {}}
                    ).status_code
                    == 403
                )
                assert (
                    sales.post(
                        api + "/tasks/" + own[0]["id"] + "/complete",
                        json={"variables": {"approved": True}},
                    ).status_code
                    == 200
                )
                event(run_id + "." + event_type, event_type, business_key)
            runtime.restart("api")
            replay = event(run_id + ".workflow.review.done", "workflow.review.done", run_id)
            assert replay["outcomes"][0]["deduped"] is True
            response = sales.get(api + "/instances/" + instance_id)
            assert response.status_code == 200 and response.json()["completed"] is True
            assert response.json()["variables"] == {}
            runtime.evidence("workflow_end_and_event_start_correlate", True)
            after = {name: business_snapshot(url) for name, url in urls.items()}
            assert after == baseline
            runtime.evidence("business_database_hashes_unchanged", True)
            runtime.evidence(
                "scope",
                "Read-only Data Product review; no reservation, shipment or business writes",
            )
    finally:
        runtime.close()
