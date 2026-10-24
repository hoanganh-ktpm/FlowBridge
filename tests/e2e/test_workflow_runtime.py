"""Real runtime acceptance probe; not a Business API/Data Sharing E2E claim."""

import asyncio
import json
import os
import subprocess
import time
from uuid import uuid4

import httpx
import pytest
from dotenv import dotenv_values
from scripts.docker_env import write_env
from scripts.e2e_environment import ROOT, RUNTIME
from scripts.e2e_runtime import NativeRuntime
from tests.e2e.workflow_fixture import prepare_identities, workflow_tokens

from app.platform.workflow.flowable import FlowableClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_real_flowable_native_api_user_task_message_and_restart():
    if os.environ.get("FLOWBRIDGE_FLOWABLE_INTEGRATION_ENABLED") != "1":
        pytest.skip("Opt-in: FLOWBRIDGE_FLOWABLE_INTEGRATION_ENABLED=1; restarts only Flowable")
    flowable = dotenv_values(ROOT / ".tmp/flowable-integration/docker.env")
    assert flowable.get("FLOWBRIDGE_FLOWABLE_PASSWORD"), "Prepare approved Flowable runtime first"
    run_id = "workflow-runtime-" + uuid4().hex[:12]
    directory = RUNTIME / "runs" / run_id
    directory.mkdir()
    (directory / "resources.json").write_text(json.dumps({"evidence": {}, "run_id": run_id}))
    write_env(
        directory / "core.env",
        {
            **dotenv_values(RUNTIME / "core.env"),
            **flowable,
            "FLOWBRIDGE_WORKFLOW_ENABLED": "true",
        },
    )
    runtime = NativeRuntime(directory, api_host="0.0.0.0")
    identity_state = prepare_identities()
    credentials = workflow_tokens(identity_state)
    key = "flowbridge_runtime_" + uuid4().hex[:12]
    bpmn = (
        (ROOT / "tests/fixtures/workflow/runtime-probe.bpmn20.xml")
        .read_text()
        .replace('id="flowbridge_runtime_probe"', 'id="' + key + '"')
    )
    try:
        runtime.start("api")
        async with httpx.AsyncClient(timeout=40) as http:
            engine = FlowableClient(
                http,
                flowable["FLOWBRIDGE_FLOWABLE_BASE_URL"],
                flowable["FLOWBRIDGE_FLOWABLE_USERNAME"],
                flowable["FLOWBRIDGE_FLOWABLE_PASSWORD"],
                35,
            )
            unauthenticated = await http.get(engine.base_url + "/management/engine")
            assert unauthenticated.status_code == 401
            assert (await engine.request("GET", "/management/engine"))["version"] == "7.2.0"
            api = runtime.base_url + "/api/v1/workflows"
            admin = {"Authorization": "Bearer " + credentials["it"]}
            sales = {"Authorization": "Bearer " + credentials["sales"]}
            disabled = {"Authorization": "Bearer " + credentials["disabled"]}
            response = await http.post(
                api + "/definitions", headers=admin, json={"name": key, "bpmn_xml": bpmn}
            )
            assert response.status_code == 201
            deployment_id = response.json()["deployment"]["id"]
            instance = await engine.start_process(
                key,
                run_id,
                {"assignee": identity_state["users"]["sales"]["id"], "order_id": run_id},
            )
            instance_id = instance["id"]
            tasks = await engine.list_tasks(process_instance_id=instance_id)
            assert len(tasks) == 1, (
                "HTTP task and gateway must reach the user task on the real engine"
            )
            task_id = tasks[0]["id"]
            runtime.evidence(
                "engine_http_gateway_user_task",
                {"deployment_id": deployment_id, "instance_id": instance_id, "task_id": task_id},
            )
            for headers in (admin, disabled):
                denied = await http.post(
                    api + "/tasks/" + task_id + "/complete", headers=headers, json={"variables": {}}
                )
                assert denied.status_code == 403
            response = await http.get(
                api + "/tasks", headers=sales, params={"process_instance_id": instance_id}
            )
            assert response.status_code == 200
            assert [item["id"] for item in response.json()["tasks"]] == [task_id]
            runtime.restart("api")
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(ROOT / ".tmp/flowable-integration/docker.env"),
                    "-f",
                    str(ROOT / "infra/flowable/docker-compose.flowable.yml"),
                    "restart",
                    "flowable",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            deadline = time.monotonic() + 150
            while True:
                try:
                    await engine.request("GET", "/management/engine")
                    break
                except Exception:
                    assert time.monotonic() < deadline, "Flowable restart readiness timed out"
                    await asyncio.sleep(2)
            assert (await engine.get_instance(instance_id))["businessKey"] == run_id
            assert (await engine.get_task(task_id))["assignee"] == identity_state["users"]["sales"][
                "id"
            ]
            runtime.evidence("engine_and_native_restart", True)
            response = await http.post(
                api + "/tasks/" + task_id + "/complete",
                headers=sales,
                json={"variables": {"approved": True}},
            )
            assert response.status_code == 200
            correlated = await engine.message_event(
                "flowbridge.probe.done", run_id, {"status": "done"}, workflow_key=key
            )
            assert correlated["id"] == instance_id
            assert (await engine.get_instance(instance_id))["completed"] is True
            runtime.evidence("user_completion_message_end", True)
            runtime.evidence(
                "scope", "Real Flowable/Keycloak/native API only; not Hasura/business E2E"
            )
    finally:
        runtime.close()
