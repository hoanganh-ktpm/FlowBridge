# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Test workflow mẫu: register/start/task/action có kiểm version.

import httpx
import pytest

from .test_contract_coverage import validate_response

KEY = {"Idempotency-Key": "sample-workflow"}


@pytest.mark.asyncio
async def test_all_workflow_operations_share_sample_binding(client: httpx.AsyncClient) -> None:
    definition = await client.post(
        "/internal/v1/core/workflow/definitions",
        headers=KEY,
        json={"key": "sample_approval", "name": "Sample approval", "bpmn_xml": "<definitions/>"},
    )
    assert definition.status_code == 201
    validate_response(definition, "WorkflowDefinition")
    assert definition.json()["name"] == "Sample approval"
    start = await client.post(
        "/internal/v1/core/workflow/instances",
        headers=KEY,
        json={
            "definition_key": definition.json()["key"],
            "business_key": "REC-SAMPLE",
            "variables": {},
        },
    )
    assert start.status_code == 201
    validate_response(start, "WorkflowInstance")
    instance = await client.get("/api/v1/core/workflow/instances/" + start.json()["id"])
    assert instance.status_code == 200 and instance.json() == start.json()
    validate_response(instance, "WorkflowInstance")
    tasks = await client.get("/api/v1/core/workflow/tasks", params={"status": "pending"})
    assert tasks.status_code == 200
    validate_response(tasks, "WorkflowTaskList")
    task = tasks.json()["items"][0]
    assert task["instance_id"] == instance.json()["id"]
    assert task["business_key"] == instance.json()["business_key"]
    url = "/api/v1/core/workflow/tasks/" + task["id"]
    detail = await client.get(url)
    assert detail.status_code == 200 and detail.json() == task
    validate_response(detail, "WorkflowTask")
    for action in ["approve", "reject"]:
        result = await client.post(
            "/internal/v1/core/workflow/tasks/" + task["id"] + "/actions",
            headers=KEY,
            json={"action": action, "expected_version": task["version"]},
        )
        assert result.status_code == 200
        validate_response(result, "WorkflowTask")
        assert result.json()["status"] == "completed" and result.json()["version"] == 2
        assert result.json()["allowed_actions"] == []
    assert (await client.get(url)).json() == task
    assert (await client.get("/api/v1/core/workflow/tasks?status=completed")).json()["total"] == 0


@pytest.mark.asyncio
async def test_workflow_errors_preserve_fixture(client: httpx.AsyncClient) -> None:
    url = "/internal/v1/core/workflow/tasks/TASK-SAMPLE/actions"
    for body, status in [
        ({"action": "approve", "expected_version": 2}, 409),
        ({"action": "unknown", "expected_version": 1}, 409),
        ({"action": " ", "expected_version": 1}, 422),
        ({"action": "approve", "expected_version": True}, 422),
    ]:
        response = await client.post(url, headers=KEY, json=body)
        assert response.status_code == status
        validate_response(response, "ErrorResponse")
    assert (await client.get("/api/v1/core/workflow/tasks/UNKNOWN")).status_code == 404
    assert (await client.get("/api/v1/core/workflow/instances/UNKNOWN")).status_code == 404
    assert (await client.get("/api/v1/core/workflow/tasks/TASK-SAMPLE")).json()["version"] == 1
