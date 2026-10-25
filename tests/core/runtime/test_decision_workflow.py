# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.decision_workflow import KEY, CoreDecisionRuns
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity
from app.platform.workflow.decision_schemas import DecisionStart, DecisionTaskCommand
from app.platform.workflow.flowable import InMemoryFlowable

from .fakes import ISSUER

pytestmark = pytest.mark.asyncio


async def fixture(harness):
    store = harness.store
    store.data["workflow_event"] = {}
    org, warehouse, order = uuid4(), uuid4(), uuid4()
    ids = {
        role: uuid4() for role in ("coordinator", "warehouse", "logistics", "manager", "executor")
    }
    for role, principal in ids.items():
        subject = "decision-" + role
        store.principals[subject] = store.principal(str(principal), "NONE", [])
        store.bindings[subject] = {
            "issuer": ISSUER,
            "subject": subject,
            "kind": "service" if role == "executor" else "human",
            "service_client_id": "decision-worker" if role == "executor" else None,
        }
    actor = VerifiedIdentity(
        ISSUER, "decision-executor", client_id="decision-worker", account_type="service"
    )
    bindings = {
        str(principal): {"org_id": str(org), "warehouse_ids": [str(warehouse)], "roles": [role]}
        for role, principal in ids.items()
        if role != "executor"
    }
    engine = InMemoryFlowable()
    await engine.deploy(
        "decision",
        "<definitions xmlns='http://www.omg.org/spec/BPMN/20100524/MODEL'><process id='"
        + KEY
        + "'/></definitions>",
    )
    data = SimpleNamespace(
        decision_order=AsyncMock(
            return_value={
                "org_id": str(org),
                "warehouse_id": str(warehouse),
                "order_id": str(order),
            }
        )
    )
    runs = CoreDecisionRuns(store, engine, data, AsyncMock(return_value=actor), bindings)
    revision = uuid4()
    body = DecisionStart(
        org_id=org,
        warehouse_id=warehouse,
        order_id=order,
        case_id=uuid4(),
        revision_id=revision,
        revision_number=1,
        participants={role: principal for role, principal in ids.items() if role != "executor"},
        check={"revision_id": revision, "input_sha256": "a" * 64, "checked_at": datetime.now(UTC)},
        order_source_id=uuid4(),
        order_contract_id=uuid4(),
    )
    return runs, actor, body, ids


async def test_scoped_roles_source_read_and_separate_machine_identity(harness):
    runs, actor, body, ids = await fixture(harness)
    started = await runs.start(actor, body, "start")
    repeat = await runs.start(
        actor,
        body.model_copy(
            update={"check": body.check.model_copy(update={"checked_at": datetime.now(UTC)})}
        ),
        "retry",
    )
    assert repeat["id"] == started["id"] and len(runs.engine.instances) == 1
    runs.data.decision_order.assert_awaited()
    assert (await runs.engine.get_instance(started["id"]))["variables"][
        "manager_assignee"
    ] == "decision-manager"
    for invalid in (
        body.model_copy(update={"warehouse_id": uuid4()}),
        body.model_copy(
            update={"participants": {**body.participants, "manager": ids["coordinator"]}}
        ),
        body.model_copy(
            update={
                "check": body.check.model_copy(
                    update={"checked_at": datetime.now(UTC) - timedelta(minutes=2)}
                )
            }
        ),
    ):
        with pytest.raises(CoreError):
            await runs.start(actor, invalid, "invalid")
    with pytest.raises(CoreError) as impersonation:
        await runs.start(VerifiedIdentity(ISSUER, "admin", client_id="portal"), body, "admin")
    assert impersonation.value.code == ErrorCode.FORBIDDEN
    runs.data.decision_order.return_value["order_id"] = str(uuid4())
    with pytest.raises(CoreError) as source:
        await runs.start(actor, body, "mixed-order")
    assert source.value.code == ErrorCode.FORBIDDEN


async def test_uncertain_start_reconciles_existing_engine_marker_without_redispatch(harness):
    runs, actor, body, _ = await fixture(harness)
    original = runs.engine.start_process
    calls = []

    async def lost_response(*args):
        calls.append(True)
        await original(*args)
        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT)

    runs.engine.start_process = lost_response
    with pytest.raises(CoreError):
        await runs.start(actor, body, "lost")
    reconciled = await runs.start(actor, body, "retry")
    assert reconciled["reconciled"] and len(calls) == len(runs.engine.instances) == 1
    changed = body.model_copy(update={"order_contract_id": uuid4()})
    with pytest.raises(CoreError) as conflict:
        await runs.start(actor, changed, "changed")
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


async def test_no_engine_match_after_uncertain_start_never_causes_blind_restart(harness):
    runs, actor, body, _ = await fixture(harness)
    runs.engine.start_process = AsyncMock(side_effect=CoreError(ErrorCode.UPSTREAM_TIMEOUT))
    for _ in range(2):
        with pytest.raises(CoreError):
            await runs.start(actor, body, "lost")
    assert runs.engine.start_process.await_count == 1


async def test_task_actor_revision_and_outcome_are_verified_not_admin_bypassed(harness):
    runs, actor, body, ids = await fixture(harness)
    started = await runs.start(actor, body, "start")
    task = runs.engine.create_user_task(started["id"], "verifyWarehouse", "decision-warehouse")
    valid = DecisionTaskCommand(
        actor_principal_id=ids["warehouse"], outcome="confirmed", check=body.check
    )
    for invalid in (
        valid.model_copy(update={"actor_principal_id": ids["manager"]}),
        valid.model_copy(update={"outcome": "approved"}),
        valid.model_copy(update={"check": body.check.model_copy(update={"revision_id": uuid4()})}),
    ):
        with pytest.raises(CoreError):
            await runs.complete(actor, started["id"], task["id"], invalid, "invalid")
    assert not task["completed"]
    completed = await runs.complete(actor, started["id"], task["id"], valid, "complete")
    repeat = await runs.complete(actor, started["id"], task["id"], valid, "complete")
    assert completed["task_id"] == repeat["task_id"]
    with pytest.raises(CoreError) as conflict:
        await runs.complete(
            actor,
            started["id"],
            task["id"],
            valid.model_copy(update={"outcome": "changes_required"}),
            "complete",
        )
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    runs.bindings.pop(str(ids["warehouse"]))
    with pytest.raises(CoreError) as revoked:
        await runs.complete(actor, started["id"], task["id"], valid, "another")
    assert revoked.value.code == ErrorCode.FORBIDDEN


async def test_dedicated_core_http_route_and_generic_task_bypass_are_guarded(harness):
    from app.core.workflow import CoreWorkflowPolicy
    from app.platform.identity.authentication import authenticated_identity
    from app.platform.workflow.router import WorkflowRuntime
    from app.platform.workflow.settings import WorkflowApiSettings

    runs, actor, body, _ = await fixture(harness)
    current = [actor]

    async def verified():
        return current[0]

    harness.app.dependency_overrides[authenticated_identity] = verified
    harness.app.state.workflow = WorkflowRuntime(
        runs.engine,
        None,
        None,
        None,
        CoreWorkflowPolicy(harness.store),
        WorkflowApiSettings(_env_file=None, allowed_workflows={KEY}),
        decisions=runs,
    )
    try:
        result = await harness.api.post(
            "/api/v1/workflows/decision-runs/commands/start",
            json=body.model_dump(mode="json"),
            headers={"Idempotency-Key": "http-start"},
        )
        assert result.status_code == 201, result.text
        task = runs.engine.create_user_task(
            result.json()["id"], "verifyWarehouse", "decision-warehouse"
        )
        task["processDefinitionId"] = KEY + ":1:definition"
        current[0] = VerifiedIdentity(ISSUER, "decision-warehouse", client_id="portal")
        bypass = await harness.api.post(
            "/api/v1/workflows/tasks/" + task["id"] + "/complete",
            json={"variables": {"approved": True}},
        )
        assert bypass.status_code == 403 and not task["completed"]
        unauthorized = await harness.api.post(
            "/api/v1/workflows/decision-runs/commands/start",
            json=body.model_dump(mode="json"),
            headers={"Idempotency-Key": "human-start"},
        )
        assert unauthorized.status_code == 403
    finally:
        harness.app.dependency_overrides.clear()
