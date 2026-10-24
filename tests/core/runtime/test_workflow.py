import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from app.core.workflow import CoreActionRegistry, CoreDataAccess, CoreEventInbox, CoreWorkflowRuns
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity
from app.platform.workflow.actions import ActionDefinition
from app.platform.workflow.flowable import InMemoryFlowable

from .fakes import ISSUER, granted, published

pytestmark = pytest.mark.asyncio


def identity(subject):
    return VerifiedIdentity(ISSUER, subject, client_id="portal", account_type="human")


def custom_action():
    return ActionDefinition(
        "warehouse.reserve",
        1,
        "warehouse",
        "HTTP",
        {"type": "object"},
        {"type": "object"},
        service_ref="warehouse",
        method="POST",
        path="/reservations",
    )


async def test_main_openapi_mounts_workflow_without_route_collisions(harness):
    schema = harness.app.openapi()
    assert "/api/v1/workflows/actions/{action_id}/execute" in schema["paths"]
    assert "/api/v1/workflows/tasks/{task_id}/complete" in schema["paths"]
    operations = [
        operation["operationId"]
        for path in schema["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operations) == len(set(operations))
    response = await harness.api.get("/api/v1/workflows", headers={"Authorization": "Bearer admin"})
    assert response.status_code == 503


async def test_custom_actions_rehydrate_from_store_and_enforce_core_admin(harness):
    await published(harness)
    harness.store.data["workflow_action"] = {}
    registry = CoreActionRegistry(harness.store)
    action = custom_action()
    options = {"actor": identity("admin"), "key": "register-action", "request_id": "wf-test"}
    await registry.register_async(action, **options)
    await CoreActionRegistry(harness.store).register_async(action, **options)
    assert len(harness.store.data["workflow_action"]) == 1
    rehydrated = await CoreActionRegistry(harness.store).resolve_async(action.action_id)
    assert rehydrated == action
    with pytest.raises(CoreError) as conflict:
        await registry.register_async(replace(action, path="/changed"), **options)
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    with pytest.raises(CoreError) as denied:
        await registry.register_async(
            replace(action, version=2),
            actor=identity("consumer"),
            key="consumer-registration",
            request_id="wf-test",
        )
    assert denied.value.code == ErrorCode.FORBIDDEN

    application = next(iter(harness.store.data["application"].values()))
    harness.store.data["application"][application.public["id"]] = replace(
        application, public={**application.public, "status": "DISABLED"}
    )
    with pytest.raises(CoreError) as inactive:
        await registry.resolve_async(action.action_id)
    assert inactive.value.code == ErrorCode.FORBIDDEN


async def test_event_inbox_retains_deduplication_when_adapter_is_recreated(harness):
    harness.store.data["workflow_event"] = {}
    calls = []

    async def dispatch():
        calls.append(True)
        await asyncio.sleep(0)
        return {"instance_id": "run-1", "event_id": "event-1"}

    first, duplicate = await asyncio.gather(
        CoreEventInbox(harness.store).once("event-1", "a" * 64, dispatch),
        CoreEventInbox(harness.store).once("event-1", "a" * 64, dispatch),
    )
    assert first["instance_id"] == duplicate["instance_id"]
    assert len(calls) == 1
    replay = await CoreEventInbox(harness.store).once("event-1", "a" * 64, dispatch)
    assert replay["deduped"] is True
    with pytest.raises(CoreError) as conflict:
        await CoreEventInbox(harness.store).once("event-1", "b" * 64, dispatch)
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


async def test_uncertain_event_is_not_blindly_dispatched_after_restart(harness):
    harness.store.data["workflow_event"] = {}
    calls = []

    async def ambiguous():
        calls.append(True)
        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT)

    with pytest.raises(CoreError):
        await CoreEventInbox(harness.store).once("ambiguous-event", "a" * 64, ambiguous)
    assert next(iter(harness.store.data["workflow_event"].values())).public["status"] == "UNCERTAIN"
    with pytest.raises(CoreError) as blocked:
        await CoreEventInbox(harness.store).once("ambiguous-event", "a" * 64, ambiguous)
    assert blocked.value.code == ErrorCode.INVALID_STATE
    assert len(calls) == 1


async def test_data_actions_use_governed_queries_and_existing_grants(harness):
    _, source, product, _, grant = await granted(harness)
    await harness.worker.run_once("workflow-grant")
    original = harness.engine.request
    reads = []

    def upstream(request):
        if request.url.path == "/v1/graphql":
            assert "x-hasura-admin-secret" not in request.headers
            assert request.headers.get("Authorization", "").startswith("Bearer ")
            reads.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "data": {
                        source["hasura_source_name"]: {"rows": [{"sku": "SKU-1", "available": 5}]}
                    }
                },
            )
        return original(request)

    harness.http._transport = httpx.MockTransport(upstream)
    port = CoreDataAccess(harness.app.state.relationship_queries)
    payload = {
        "product_key": product["product_key"],
        "contract_version": "1.0.0",
        "filters": {"sku": "SKU-1"},
        "columns": ["sku", "available"],
    }
    record = await port.execute("data.get_record", payload, identity("consumer"), "wf-data")
    assert record == {"sku": "SKU-1", "available": 5}
    result = await port.execute("data.query", payload, identity("consumer"), "wf-data")
    assert result["rows"] == [record]
    assert len(reads) == 2
    resource = harness.store.data["grant"][grant["id"]]
    harness.store.data["grant"][grant["id"]] = replace(
        resource, public={**resource.public, "status": "REVOKED"}
    )
    with pytest.raises(CoreError) as denied:
        await port.execute("data.query", payload, identity("consumer"), "wf-data")
    assert denied.value.code == ErrorCode.FORBIDDEN
    assert len(reads) == 2
    with pytest.raises(CoreError) as write:
        await port.execute("data.update", payload, identity("admin"), "wf-data")
    assert write.value.code == ErrorCode.FORBIDDEN


async def test_event_intent_survives_cancelled_dispatch(harness):
    harness.store.data["workflow_event"] = {}
    started = asyncio.Event()

    async def dispatch():
        started.set()
        await asyncio.Event().wait()

    key = str(uuid4())
    task = asyncio.create_task(CoreEventInbox(harness.store).once(key, "a" * 64, dispatch))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(CoreError) as blocked:
        await CoreEventInbox(harness.store).once(key, "a" * 64, dispatch)
    assert blocked.value.code == ErrorCode.INVALID_STATE


async def test_run_start_binds_execution_identity_and_durable_intent(harness):
    from unittest.mock import AsyncMock

    harness.store.data["workflow_event"] = {}
    engine = InMemoryFlowable()
    await engine.deploy(
        "read.bpmn20.xml",
        "<definitions xmlns='http://www.omg.org/spec/BPMN/20100524/MODEL'>"
        "<process id='read'/></definitions>",
    )
    actor = identity("consumer")
    data = AsyncMock()
    execution_identity = AsyncMock(return_value=actor)
    runs = CoreWorkflowRuns(harness.store, engine, data, execution_identity)
    variables = {
        "data_request": {
            "product_key": "warehouse.products",
            "contract_version": "1.0.0",
            "filters": {"id": "row-1"},
        },
        "custom_request": {"select": ["id"]},
    }
    instance = await runs.start(actor, "read", "order-1", variables, "start-1", "wf-test")
    replay = await CoreWorkflowRuns(harness.store, engine, data, execution_identity).start(
        actor, "read", "order-1", variables, "start-1", "wf-test"
    )
    assert replay["id"] == instance["id"]
    assert replay["deduped"] is True
    assert len(engine.instances) == 1
    await runs.authorize(actor, instance["id"])
    with pytest.raises(CoreError) as other:
        await runs.authorize(identity("admin"), instance["id"])
    assert other.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(CoreError) as impersonation:
        await runs.start(identity("admin"), "read", "order-2", variables, "start-2", "wf-test")
    assert impersonation.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(CoreError) as conflict:
        await runs.start(actor, "read", "order-2", variables, "start-1", "wf-test")
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
