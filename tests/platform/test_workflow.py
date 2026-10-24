# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Offline contract tests for the Layer 1 Workflow module. Uses the in-memory
# engine double and a mock action gateway; no Supabase/Hasura/Flowable needed.
# Live engine persistence tests are documented as BLOCKED below and require
# docker + Supabase credentials on the operator machine.

from __future__ import annotations

from time import time

import httpx
import pytest
import pytest_asyncio

from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.settings import IdentitySettings
from app.platform.workflow.actions import ActionDefinition, ActionRegistry
from app.platform.workflow.events import EventRouter, EventSubscription
from app.platform.workflow.flowable import InMemoryFlowable
from app.platform.workflow.gateway import ActionGateway, DataAccessPort
from app.platform.workflow.router import create_workflow_app, validate_bpmn
from app.platform.workflow.settings import WorkflowApiSettings

ISSUER = "https://identity.example.invalid/realm"


class MemoryData(DataAccessPort):
    def __init__(self):
        self.stock = {"SKU-1": 5}
        self.reads = 0

    async def read(self, payload):
        self.reads += 1
        sku = payload.get("filters", {}).get("sku", "SKU-1")
        return {"sku": sku, "available_quantity": self.stock.get(sku, 0)}

    async def write(self, payload):
        sku = payload.get("filters", {}).get("sku", "SKU-1")
        self.stock[sku] = int(payload.get("patch", {}).get("available_quantity", 0))
        return {"sku": sku, "updated": True}

    async def execute(self, action_id, payload, actor, request_id):
        return await self.read(payload)


class TestOnlyAdminPolicy:
    async def require_actor(self, actor):
        return None

    async def require_admin(self, actor, operation):
        if actor.subject != "it-admin":
            raise CoreError(ErrorCode.FORBIDDEN)


class MemoryInbox:
    def __init__(self):
        self.records = {}

    async def once(self, key, digest, dispatch):
        if key in self.records:
            saved_hash, result = self.records[key]
            if digest != saved_hash:
                raise CoreError(ErrorCode.IDEMPOTENCY_CONFLICT)
            return {**result, "deduped": True}
        result = await dispatch()
        self.records[key] = (digest, result)
        return result


class MemoryRuns:
    def __init__(self, engine):
        self.engine = engine
        self.owners = {}

    async def start(self, actor, key, business_key, variables, idempotency_key, request_id):
        instance = await self.engine.start_process(key, business_key, variables)
        self.owners[instance["id"]] = (actor.issuer, actor.subject)
        return instance

    async def authorize(self, actor, instance_id):
        if self.owners.get(instance_id) != (actor.issuer, actor.subject):
            raise CoreError(ErrorCode.FORBIDDEN)


@pytest_asyncio.fixture
async def workflow_client():
    engine = InMemoryFlowable()
    registry = ActionRegistry()
    data = MemoryData()
    gateway = ActionGateway(registry=registry, data=data)

    def warehouse(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content or b"{}")
        return httpx.Response(200, json={"reserved": True, "order_id": body.get("order_id")})

    http = httpx.AsyncClient(transport=httpx.MockTransport(warehouse))
    from app.platform.workflow.gateway import AllowlistHttpPort

    registry.register(
        ActionDefinition(
            action_id="warehouse.reserve_stock",
            version=1,
            owner_application="warehouse",
            implementation_type="HTTP",
            input_schema={
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
            output_schema={"type": "object"},
            service_ref="warehouse",
            method="POST",
            path="/reserve",
        )
    )
    gateway.http_port = AllowlistHttpPort(http, frozenset({"warehouse.test"}), 5.0)
    gateway.service_bases = {"warehouse": "https://warehouse.test"}
    router = EventRouter(engine=engine, inbox=MemoryInbox(), webhook_secret="test-webhook-secret")
    router.subscribe(
        EventSubscription(
            subscription_id="sub-1",
            event_type="shipment.delivered",
            workflow_key="order_fulfillment",
            mode="CORRELATE",
            message_name="shipment.delivered",
        )
    )

    def introspect(request: httpx.Request) -> httpx.Response:
        token = (request.content.decode()).split("token=")[-1].split("&")[0]
        scopes = "PLATFORM_ADMIN" if token == "it-admin" else ""
        return httpx.Response(
            200,
            json={
                "active": True,
                "iss": ISSUER,
                "sub": "warehouse-user" if token != "it-admin" else "it-admin",
                "aud": ["flowbridge"],
                "exp": time() + 600,
                "token_type": "Bearer",
                "client_id": "flowbridge-it" if token == "it-admin" else "warehouse-app",
                "scope": scopes,
            },
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(introspect))
    settings = IdentitySettings(
        _env_file=None,
        oidc_issuer=ISSUER,
        oidc_audience="flowbridge",
        oidc_introspection_url="https://identity.example.invalid/introspect",
        oidc_client_id="backend",
        oidc_client_secret="only-a-test-secret",
    )
    app = create_workflow_app(
        engine,
        registry,
        gateway,
        router,
        settings,
        admin_policy=TestOnlyAdminPolicy(),
        testing=True,
        runs=MemoryRuns(engine),
        configuration=WorkflowApiSettings(
            _env_file=None, allowed_workflows=frozenset({"order_fulfillment"})
        ),
    )
    from types import SimpleNamespace

    app.state.settings = settings
    app.state.resources = SimpleNamespace(http=upstream)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, engine, registry, gateway, router, data
    await http.aclose()
    await upstream.aclose()


def headers(token="warehouse-token"):
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-Id": "wf-test-1",
        "Idempotency-Key": "test-command-1",
    }


def it_headers():
    return {"Authorization": "Bearer it-admin", "X-Correlation-Id": "wf-test-1"}


@pytest.mark.asyncio
async def test_bpmn_validation_accepts_order_fulfillment():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    bpmn = (root / "workflows" / "order_fulfillment.bpmn").read_text()
    assert validate_bpmn(bpmn) == "order_fulfillment"


@pytest.mark.asyncio
async def test_bpmn_validation_rejects_non_xml():
    import pytest as _pytest

    from app.platform.http.errors import CoreError

    with _pytest.raises(CoreError):
        validate_bpmn("not xml at all <")


_BPMN_SNIPPET = (
    "<bpmn:definitions xmlns:bpmn='http://www.omg.org/spec/BPMN/20100524/MODEL'>"
    "<bpmn:process id='order_fulfillment'/></bpmn:definitions>"
)


@pytest.mark.asyncio
async def test_publish_requires_it_admin(workflow_client):
    client, *_ = workflow_client
    response = await client.post(
        "/api/v1/workflows/definitions",
        headers=headers(),
        json={"name": "order_fulfillment", "bpmn_xml": _BPMN_SNIPPET},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_workflow_api_with_in_memory_engine(workflow_client):
    client, engine, registry, gateway, router, data = workflow_client
    response = await client.post(
        "/api/v1/workflows/definitions",
        headers=it_headers(),
        json={"name": "order_fulfillment", "bpmn_xml": _BPMN_SNIPPET},
    )
    assert response.status_code == 201, response.text

    # Sales starts workflow with order_id.
    response = await client.post(
        "/api/v1/workflows/order_fulfillment/start",
        headers=headers(),
        json={"business_key": "ORDER-1", "variables": {"order_id": "ORDER-1"}},
    )
    assert response.status_code == 201, response.text
    instance_id = response.json()["instance_id"]

    # Data Action reads inventory through the gateway.
    result = await gateway.execute(
        "data.query",
        {
            "product_key": "warehouse.inventory",
            "contract_version": "1.0.0",
            "filters": {"sku": "SKU-1"},
        },
    )
    assert result["available_quantity"] == 5
    assert data.reads == 1

    # Custom Action reserves stock via registered HTTP endpoint only.
    result = await gateway.execute("warehouse.reserve_stock", {"order_id": "ORDER-1"})
    assert result == {"reserved": True, "order_id": "ORDER-1"}

    # Warehouse user task created and completed through the Workflow API.
    engine.create_user_task(instance_id, "warehouse_pack", assignee="warehouse-user")
    response = await client.get(
        "/api/v1/workflows/tasks",
        headers=headers(),
        params={"process_instance_id": instance_id, "mine": "true"},
    )
    assert response.status_code == 200, response.text
    task_id = response.json()["tasks"][0]["id"]
    response = await client.post(
        f"/api/v1/workflows/tasks/{task_id}/complete",
        headers=headers(),
        json={"variables": {"packed": True}},
    )
    assert response.status_code == 200, response.text

    # Event router correlates shipment.delivered to the right instance.
    outcomes = await router.receive(
        {
            "event_id": "evt-1",
            "event_type": "shipment.delivered",
            "payload": {"business_key": "ORDER-1", "status": "delivered"},
        }
    )
    assert outcomes[0]["instance_id"] == instance_id

    # Duplicate event is deduplicated, wrong correlation is isolated.
    outcomes = await router.receive(
        {
            "event_id": "evt-1",
            "event_type": "shipment.delivered",
            "payload": {"business_key": "ORDER-1", "status": "delivered"},
        }
    )
    assert outcomes[0]["deduped"] is True

    # Status endpoint exposes only public variables.
    response = await client.get(f"/api/v1/workflows/instances/{instance_id}", headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["business_key"] == "ORDER-1"


@pytest.mark.asyncio
async def test_action_validation_and_override_rules(workflow_client):
    client, engine, registry, gateway, router, data = workflow_client
    import pytest as _pytest

    from app.platform.http.errors import CoreError

    with _pytest.raises(CoreError):
        await gateway.execute("data.query", {"product_key": "x"})
    base = registry.resolve("data.query")
    clone = ActionDefinition(
        action_id=base.action_id,
        version=2,
        owner_application="warehouse",
        implementation_type="HTTP",
        input_schema={"type": "object", "properties": {"other": {"type": "string"}}},
        output_schema=base.output_schema,
        status="ACTIVE",
        service_ref="warehouse",
        method="POST",
        path="/other",
        scope="warehouse",
        overrides="data.query@1",
    )
    with _pytest.raises(CoreError):
        registry.register(clone)
    compatible = ActionDefinition(
        action_id=base.action_id,
        version=2,
        owner_application="warehouse",
        implementation_type="HTTP",
        input_schema=base.input_schema,
        output_schema=base.output_schema,
        status="ACTIVE",
        service_ref="warehouse",
        method="POST",
        path="/scoped-query",
        scope="warehouse",
        overrides="data.query@1",
    )
    registry.register(compatible)
    assert registry.resolve("data.query", scope="warehouse").version == 2
    assert registry.resolve("data.query").version == 1


@pytest.mark.asyncio
async def test_task_completion_enforces_assignee(workflow_client):
    client, engine, *_ = workflow_client
    await engine.deploy("order.bpmn20.xml", _BPMN_SNIPPET)
    instance = await engine.start_process("order_fulfillment", "ORDER-9", {})
    task = engine.create_user_task(instance["id"], "warehouse_pack", assignee="someone-else")
    response = await client.post(
        f"/api/v1/workflows/tasks/{task['id']}/complete",
        headers=headers(),
        json={"variables": {}},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_workflow_does_not_expose_process_variables(workflow_client):
    client, engine, *_ = workflow_client
    await engine.deploy("order.bpmn20.xml", _BPMN_SNIPPET)
    response = await client.post(
        "/api/v1/workflows/order_fulfillment/start",
        headers=headers(),
        json={"business_key": "redaction", "variables": {"order_id": "redaction"}},
    )
    instance_id = response.json()["instance_id"]
    engine.instances[instance_id]["variables"] = {"password": "must-not-leak", "token": "hidden"}
    response = await client.get(f"/api/v1/workflows/instances/{instance_id}", headers=headers())
    assert response.json()["variables"] == {}
    assert "must-not-leak" not in response.text


@pytest.mark.asyncio
async def test_unassigned_task_is_not_open_to_any_user(workflow_client):
    client, engine, *_ = workflow_client
    await engine.deploy("order.bpmn20.xml", _BPMN_SNIPPET)
    instance = await engine.start_process("order_fulfillment", "unassigned", {})
    task = engine.create_user_task(instance["id"], "packing")
    response = await client.post(
        f"/api/v1/workflows/tasks/{task['id']}/complete",
        headers=it_headers(),
        json={"variables": {}},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_events_require_valid_hmac(workflow_client):
    import hashlib
    import hmac
    import json

    client, *_ = workflow_client
    body = json.dumps({"event_id": "signed-event", "event_type": "none", "payload": {}}).encode()
    assert (
        await client.post(
            "/api/v1/workflows/events", content=body, headers={"Content-Type": "application/json"}
        )
    ).status_code == 401
    signature = hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    response = await client.post(
        "/api/v1/workflows/events",
        content=body,
        headers={"Content-Type": "application/json", "X-Event-Signature": signature},
    )
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_data_update_is_not_an_authorization_bypass(workflow_client):
    *_, gateway, router, data = workflow_client
    with pytest.raises(CoreError) as error:
        await gateway.execute(
            "data.update",
            {
                "product_key": "warehouse.inventory",
                "contract_version": "1.0.0",
                "filters": {"sku": "SKU-1"},
                "patch": {},
            },
        )
    assert error.value.code == ErrorCode.INVALID_STATE


def test_event_hmac_without_secret_fails_closed():
    with pytest.raises(CoreError) as error:
        EventRouter(InMemoryFlowable()).verify(b"{}", None)
    assert error.value.code == ErrorCode.UPSTREAM_UNAVAILABLE


@pytest.mark.asyncio
async def test_trigger_payload_builds_safe_metadata_call():
    from app.platform.workflow.triggers import WorkflowTriggerSpec, trigger_payload

    payload = trigger_payload(
        WorkflowTriggerSpec(
            name="inventory_updated",
            source="warehouse",
            schema="public",
            table="stock_available",
            operations=("UPDATE",),
            columns=("sku",),
            webhook_url="https://workflow.example.invalid/api/v1/workflows/events",
        )
    )
    assert payload["type"] == "pg_create_event_trigger"
    assert payload["args"]["webhook"].startswith("https://")
    assert "admin" not in str(payload).lower()
