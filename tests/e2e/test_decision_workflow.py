# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual BPMN engine semantics on isolated Flowable/PostgreSQL, not user SSO E2E."""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app.platform.workflow.flowable import FlowableClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def engine():
    base = os.environ.get("FLOWBRIDGE_DECISION_FLOWABLE_TEST_URL")
    if not base or not os.environ.get("FLOWBRIDGE_DECISION_TEST_RUN"):
        pytest.skip("Use scripts.decision_workflow_test_environment for isolated Flowable")
    assert base.startswith("http://127.0.0.1:") and base.endswith("/flowable-rest/service")
    async with httpx.AsyncClient(timeout=30) as http:
        assert (await http.get(base + "/management/engine")).status_code == 401
        yield FlowableClient(
            http,
            base,
            os.environ["FLOWBRIDGE_DECISION_FLOWABLE_TEST_USER"],
            os.environ["FLOWBRIDGE_DECISION_FLOWABLE_TEST_PASSWORD"],
            30,
        )


async def start(engine, *, quick_timers=False):
    key = "decision_acceptance_" + uuid4().hex
    template = (ROOT / "workflows/supply_handover_exception.bpmn20.xml").read_text(encoding="utf-8")
    xml = template.replace('id="supply_handover_exception"', 'id="' + key + '"')
    if quick_timers:
        xml = xml.replace("PT24H", "PT2S")
    await engine.deploy(key, xml)
    roles = {
        role + "_assignee": role + "-" + uuid4().hex
        for role in ("coordinator", "warehouse", "logistics", "manager")
    }
    instance = await engine.start_process(key, str(uuid4()), roles)
    return key, instance, roles


@pytest.mark.parametrize("outcome", ["approved", "changes_required", "rejected"])
async def test_parallel_verification_manager_outcomes_and_actual_handoff_wait(engine, outcome):
    key, instance, roles = await start(engine)
    identifier = instance["id"]
    tasks = {
        t["taskDefinitionKey"]: t for t in await engine.list_tasks(process_instance_id=identifier)
    }
    assert set(tasks) == {"verifyWarehouse", "verifyLogistics"}
    assert tasks["verifyWarehouse"]["assignee"] == roles["warehouse_assignee"]
    assert tasks["verifyLogistics"]["assignee"] == roles["logistics_assignee"]
    await engine.complete_task(tasks["verifyWarehouse"]["id"], {"warehouse_outcome": "confirmed"})
    remaining = await engine.list_tasks(process_instance_id=identifier)
    assert [t["taskDefinitionKey"] for t in remaining] == ["verifyLogistics"]
    await engine.complete_task(tasks["verifyLogistics"]["id"], {"logistics_outcome": "confirmed"})
    manager = (await engine.list_tasks(process_instance_id=identifier))[0]
    assert manager["taskDefinitionKey"] == "managerReview"
    assert manager["assignee"] == roles["manager_assignee"]
    await engine.complete_task(manager["id"], {"manager_outcome": outcome})
    if outcome == "approved":
        assert not (await engine.get_instance(identifier)).get("completed", False)
        assert await engine.list_tasks(process_instance_id=identifier) == []
        # Engine-only event probe. Production correlation must verify source proof in M09.
        await engine.message_event(
            "decision.carrier_handed_over", instance["businessKey"], {}, workflow_key=key
        )
    finished = await engine.get_instance(identifier)
    assert finished["completed"] is True
    assert (
        finished["endActivityId"]
        == {
            "approved": "handedOver",
            "changes_required": "revisionRequired",
            "rejected": "stopped",
        }[outcome]
    )


async def test_failed_verification_does_not_reach_manager(engine):
    _, instance, _ = await start(engine)
    tasks = {
        t["taskDefinitionKey"]: t
        for t in await engine.list_tasks(process_instance_id=instance["id"])
    }
    await engine.complete_task(
        tasks["verifyLogistics"]["id"], {"logistics_outcome": "changes_required"}
    )
    assert [
        t["taskDefinitionKey"] for t in await engine.list_tasks(process_instance_id=instance["id"])
    ] == ["verifyWarehouse"]
    await engine.complete_task(tasks["verifyWarehouse"]["id"], {"warehouse_outcome": "confirmed"})
    assert (await engine.get_instance(instance["id"]))["endActivityId"] == "revisionRequired"


async def test_core_completion_requires_current_scoped_physical_proof_and_reconciles_message_loss(
    engine,
):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.core.runtime.fakes import MemoryStore
    from tests.core.runtime.test_decision_workflow import fixture as core_fixture

    from app.platform.http.errors import CoreError, ErrorCode
    from app.platform.workflow.decision_schemas import DecisionHandoffCommand, DecisionTaskCommand

    runs, actor, body, principals = await core_fixture(SimpleNamespace(store=MemoryStore()))
    runs.engine = engine
    await engine.deploy(
        "decision-proof",
        (ROOT / "workflows/supply_handover_exception.bpmn20.xml").read_text(encoding="utf-8"),
    )
    started = await runs.start(actor, body, "proof-start")
    identifier = started["id"]
    command = DecisionHandoffCommand(
        revision_id=body.revision_id, actor_principal_id=principals["coordinator"]
    )
    instant = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    proof = {
        "org_id": str(body.org_id),
        "warehouse_id": str(body.warehouse_id),
        "order_id": str(body.order_id),
        "source_order_revision": 1,
        "complete": True,
        "completed_at": instant,
        "evidence": [
            {
                "source_event_id": str(uuid4()),
                "dispatch_id": str(uuid4()),
                "source_version": 2,
                "manifest_hash": "a" * 64,
                "handed_over_at": instant,
            }
        ],
        "lines": [
            {
                "order_line_id": str(uuid4()),
                "sku": "COFFEE-500G",
                "uom": "gói",
                "ordered_quantity": "4.000",
                "handed_over_quantity": "4.000",
            }
        ],
    }
    runs.handoff_reader = AsyncMock(return_value=proof)
    with pytest.raises(CoreError) as early:
        await runs.confirm_handoff(actor, identifier, command, "worker-token", "early")
    assert early.value.code == ErrorCode.INVALID_STATE
    assert len(await engine.list_tasks(process_instance_id=identifier)) == 2
    tasks = await engine.list_tasks(process_instance_id=identifier)
    for role, task_key in (("warehouse", "verifyWarehouse"), ("logistics", "verifyLogistics")):
        task = next(t for t in tasks if t["taskDefinitionKey"] == task_key)
        await runs.complete(
            actor,
            identifier,
            task["id"],
            DecisionTaskCommand(
                actor_principal_id=principals[role], outcome="confirmed", check=body.check
            ),
            "confirm-" + role,
        )
    task = (await engine.list_tasks(process_instance_id=identifier))[0]
    await runs.complete(
        actor,
        identifier,
        task["id"],
        DecisionTaskCommand(
            actor_principal_id=principals["manager"], outcome="approved", check=body.check
        ),
        "approve",
    )
    for invalid in (
        {**proof, "complete": False},
        {**proof, "org_id": str(uuid4())},
        {**proof, "lines": [{**proof["lines"][0], "handed_over_quantity": "3.000"}]},
    ):
        runs.handoff_reader.return_value = invalid
        with pytest.raises(CoreError):
            await runs.confirm_handoff(actor, identifier, command, "worker-token", "invalid")
        assert await engine.awaits_message(identifier, "decision.carrier_handed_over")
    runs.handoff_reader.return_value = proof
    original = engine.message_event
    calls = []

    async def lost(*args, **kwargs):
        calls.append(True)
        await original(*args, **kwargs)
        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT)

    engine.message_event = lost
    with pytest.raises(CoreError):
        await runs.confirm_handoff(actor, identifier, command, "worker-token", "lost")
    complete = await runs.confirm_handoff(actor, identifier, command, "worker-token", "retry")
    assert complete["reconciled"] and complete["source_event_ids"] == [
        proof["evidence"][0]["source_event_id"]
    ]
    assert len(calls) == 1
    assert (await engine.get_instance(identifier))["endActivityId"] == "handedOver"


async def test_owned_engine_restart_keeps_parallel_tasks_and_timers_never_approve(engine):
    _, instance, roles = await start(engine, quick_timers=True)
    identifier = instance["id"]
    before = {t["id"] for t in await engine.list_tasks(process_instance_id=identifier)}
    container = os.environ["FLOWBRIDGE_DECISION_FLOWABLE_TEST_CONTAINER"]
    run_id = os.environ["FLOWBRIDGE_DECISION_TEST_RUN"]
    from scripts.decision_test_environment import LABEL, docker

    current = json.loads(docker("inspect", container))[0]
    assert current["Id"] == container and current["Config"]["Labels"][LABEL] == run_id
    assert current["Name"] == "/decision-flowable-" + run_id
    await asyncio.to_thread(
        subprocess.run,
        ["docker", "restart", container],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    # Docker Desktop can reassign a dynamically published port on restart.
    port = int(docker("port", container, "18081/tcp").rsplit(":", 1)[1])
    engine.base_url = f"http://127.0.0.1:{port}/flowable-rest/service"
    for attempt in range(90):
        try:
            await engine.request("GET", "/management/engine")
            break
        except Exception:
            assert attempt < 89, "Isolated engine did not recover"
            await asyncio.sleep(2)
    for attempt in range(35):
        tasks = await engine.list_tasks(process_instance_id=identifier)
        if {t["taskDefinitionKey"] for t in tasks} == {
            "verifyWarehouse",
            "verifyLogistics",
            "warehouseReminder",
            "logisticsReminder",
        }:
            break
        assert attempt < 34, "Expected persisted non-interrupting timer reminders"
        await asyncio.sleep(1)
    assert before.issubset({t["id"] for t in tasks})
    assert all(
        t["assignee"] == roles["coordinator_assignee"]
        for t in tasks
        if t["taskDefinitionKey"].endswith("Reminder")
    )
    assert not (await engine.get_instance(identifier)).get("completed", False)
    assert not any(t["taskDefinitionKey"] == "managerReview" for t in tasks)
