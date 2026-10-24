"""Live Sales -> Warehouse -> Logistics -> Hasura event -> Flowable End."""

import asyncio
import hashlib
import hmac
import json
import os
import time
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from scripts.business_environment import (
    RUNTIME,
    BusinessProcesses,
    compose,
    configure,
    finish_event_sources,
    historical_snapshot,
    prepare,
    prepare_event_sources,
)
from scripts.database.engine import make_engine
from scripts.database.registry import REPOSITORY_ROOT
from scripts.e2e_runtime import NativeRuntime
from scripts.integration_utils import tokens
from tests.e2e.test_automated_onboarding import call, wait_job
from tests.e2e.test_business_apis import create_order, request

from app.core.hasura_provisioner.client import HasuraMetadataClient
from app.core.hasura_provisioner.settings import HasuraSettings
from app.platform.workflow.triggers import WorkflowTriggerSpec, drop_trigger, provision_trigger

pytestmark = pytest.mark.e2e


def wait_until(fn, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.25)
    raise AssertionError("Timed out waiting for real workflow/event state")


def engine_request(live, method, path, body=None, expected=200):
    response = httpx.request(
        method,
        "http://127.0.0.1:18083/flowable-rest/service" + path,
        auth=("rest-admin", live.secrets["FLOWABLE_REST_PASSWORD"]),
        json=body,
        timeout=45,
    )
    assert response.status_code == expected, f"Flowable {path}: {response.status_code}"
    return response.json() if response.content else {}


def setup_triggers(live):
    async def setup():
        settings = HasuraSettings(
            endpoint=live.env["FLOWBRIDGE_HASURA_ENDPOINT"],
            admin_secret=SecretStr(live.env["FLOWBRIDGE_HASURA_ADMIN_SECRET"]),
        )
        async with httpx.AsyncClient(timeout=30) as http:
            client = HasuraMetadataClient(settings, http)
            for domain, table, op in (
                ("sales", "wf_e2e_orders", "INSERT"),
                ("logistics", "wf_e2e_shipments", "UPDATE"),
            ):
                source = f"wf_e2e_{domain}_events"
                snapshot = await client.export_metadata()
                existing = next(
                    (s for s in snapshot.metadata.get("sources", []) if s["name"] == source), None
                )
                if existing is None:
                    await client._metadata(
                        "pg_add_source",
                        {
                            "name": source,
                            "configuration": {
                                "connection_info": {
                                    "database_url": {
                                        "from_env": f"WF_E2E_{domain.upper()}_EVENTS_URL"
                                    }
                                }
                            },
                        },
                    )
                    existing = {"tables": []}
                if not any(
                    t["table"] == {"schema": "public", "name": table} for t in existing["tables"]
                ):
                    await client._metadata(
                        "pg_track_table",
                        {"source": source, "table": {"schema": "public", "name": table}},
                    )
                spec = WorkflowTriggerSpec(
                    name=live.resources["run_id"] + "_" + domain,
                    source=source,
                    schema="public",
                    table=table,
                    operations=(op,),
                    webhook_url="http://host.docker.internal:18000/api/v1/workflows/hasura-events",
                    columns=("status",) if op == "UPDATE" else (),
                    headers=(
                        {
                            "name": "X-Hasura-Event-Secret",
                            "value_from_env": "WF_E2E_EVENT_HEADER_SECRET",
                        },
                        {"name": "X-FlowBridge-Source", "value": source},
                    ),
                    allow_local_callback=True,
                )
                await provision_trigger(client, spec)
                live.resources.setdefault("capture_triggers", []).append(
                    {"source": source, "name": spec.name}
                )
                live.save()

    asyncio.run(setup())
    finish_event_sources()


def cleanup_triggers(live):
    async def cleanup():
        settings = HasuraSettings(
            endpoint=live.env["FLOWBRIDGE_HASURA_ENDPOINT"],
            admin_secret=SecretStr(live.env["FLOWBRIDGE_HASURA_ADMIN_SECRET"]),
        )
        async with httpx.AsyncClient(timeout=20) as http:
            client = HasuraMetadataClient(settings, http)
            for trigger in live.resources.get("capture_triggers", []):
                await drop_trigger(client, trigger["source"], trigger["name"])

    asyncio.run(cleanup())


@pytest.fixture(scope="module")
def live():
    if os.environ.get("FLOWBRIDGE_BUSINESS_WORKFLOW_E2E") != "1":
        pytest.skip("Opt-in FLOWBRIDGE_BUSINESS_WORKFLOW_E2E=1; only new local E2E rows")
    configure()
    prepare_event_sources()
    from scripts.database.local import compose as postgres_compose

    postgres_compose("--profile", "integration", "up", "-d", "hasura")
    compose("up", "-d")
    directory = prepare()
    runtime = NativeRuntime(directory, api_host="0.0.0.0")
    runtime.secrets = json.loads((RUNTIME / "secrets.json").read_text())
    runtime.auth = tokens()
    runtime.clients = {
        label: httpx.Client(
            base_url=runtime.base_url,
            timeout=50,
            headers={"Authorization": "Bearer " + runtime.auth[label]},
        )
        for label in ("it", "sales", "outsider")
    }
    processes = BusinessProcesses(directory)
    runtime.business = processes
    runtime.grant = None
    try:
        for domain in ("sales", "warehouse", "logistics"):
            processes.start(domain)
        deadline = time.monotonic() + 90
        while True:
            try:
                assert engine_request(runtime, "GET", "/management/engine")["version"] == "7.2.0"
                break
            except (httpx.HTTPError, AssertionError):
                assert time.monotonic() < deadline, "Local Flowable not ready"
                time.sleep(1)
        runtime.start("api")
        runtime.start("worker")
        it = runtime.clients["it"]
        core = make_engine("core")
        try:
            with core.connect() as conn:
                product = (
                    conn.execute(
                        sa.text(
                            "SELECT id,active_contract_id FROM data_products "
                            "WHERE product_key='warehouse.stock_availability' AND status='ACTIVE'"
                        )
                    )
                    .mappings()
                    .one()
                )
                department = conn.scalar(sa.text("SELECT id FROM departments WHERE code='sales'"))
        finally:
            core.dispose()
        runtime.product = {k: str(v) for k, v in product.items()}
        columns = ["org_id", "warehouse_id", "product_id", "sku", "available_quantity"]
        scope = {"action": "SELECT", "columns": columns, "row_scope": {"kind": "ALL"}}
        requested = call(
            it,
            "POST",
            "/access-requests",
            201,
            json={
                "data_product_id": str(product["id"]),
                "contract_version_id": str(product["active_contract_id"]),
                "consumer_department_id": str(department),
                "consumer_principal_id": None,
                "requested_scope_json": scope,
                "reason": runtime.resources["run_id"],
            },
        )
        approved = call(
            it,
            "POST",
            "/access-requests/" + requested["id"] + "/approve",
            202,
            json={
                "expected_revision": requested["revision"],
                "scope_json": scope,
                "expires_at": None,
            },
        )
        runtime.grant = approved["grant"]["id"]
        wait_job(it, approved["job"])
        schemas = json.loads(
            (REPOSITORY_ROOT / "contracts/workflow-business.schema.json").read_text()
        )["$defs"]
        for action, owner, path, schema in (
            (
                "warehouse.reserve_stock",
                "warehouse",
                "/api/v1/warehouse/workflow/reservations",
                "order_ref",
            ),
            (
                "logistics.create_shipment",
                "logistics",
                "/api/v1/logistics/workflow/shipments",
                "order_ref",
            ),
            ("sales.update_order", "sales", "/api/v1/sales/workflow/orders/status", "order_status"),
        ):
            core = make_engine("core")
            try:
                with core.connect() as conn:
                    version = (
                        conn.scalar(
                            sa.text(
                                "SELECT max(version) FROM workflow_actions WHERE action_id=:action"
                            ),
                            {"action": action},
                        )
                        or 0
                    )
            finally:
                core.dispose()
            response = it.post(
                "/api/v1/workflows/actions",
                headers={"Idempotency-Key": uuid4().hex},
                json={
                    "action_id": action,
                    "version": version + 1,
                    "owner_application": owner,
                    "implementation_type": "HTTP",
                    "input_schema": schemas[schema],
                    "output_schema": schemas["record"],
                    "service_ref": owner,
                    "method": "POST",
                    "path": path,
                    "timeout_seconds": 15,
                },
            )
            assert response.status_code == 201, (
                f"Action registration: {response.status_code} {response.text}"
            )
        bpmn = (REPOSITORY_ROOT / "workflows/order_fulfillment.bpmn").read_text(encoding="utf-8")
        response = it.post(
            "/api/v1/workflows/definitions",
            json={"name": "wf_e2e_order_fulfillment", "bpmn_xml": bpmn},
        )
        assert response.status_code == 201, response.text
        runtime.resources["deployment_id"] = response.json()["deployment"]["id"]
        probe = """<?xml version="1.0"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" targetNamespace="wf_e2e">
  <process id="wf_e2e_event_start" isExecutable="true">
    <startEvent id="start"/><sequenceFlow id="f" sourceRef="start" targetRef="end"/>
    <endEvent id="end"/>
  </process>
</definitions>"""
        response = it.post(
            "/api/v1/workflows/definitions", json={"name": "wf_e2e_start_probe", "bpmn_xml": probe}
        )
        assert response.status_code == 201, response.text
        setup_triggers(runtime)
        runtime.save()
        yield runtime
    finally:
        try:
            if "api" in runtime.processes:
                # Drop only capture triggers created by this run. No record/database reset.
                cleanup_triggers(runtime)
                if runtime.grant:
                    grant = call(runtime.clients["it"], "GET", "/access-grants/" + runtime.grant)
                    if grant["status"] == "ACTIVE":
                        revoked = call(
                            runtime.clients["it"],
                            "POST",
                            "/access-grants/" + runtime.grant + "/revoke",
                            202,
                            json={
                                "expected_desired_revision": grant["desired_revision"],
                                "reason": "E2E complete",
                            },
                        )
                        wait_job(runtime.clients["it"], revoked)
        finally:
            runtime.close()
            processes.close()
            for client in runtime.clients.values():
                client.close()
            assert historical_snapshot() == runtime.resources["historical_before"]


def start(live, order):
    ref = {"org_id": live.resources["org_id"], "order_id": order["id"]}
    realm = json.loads((REPOSITORY_ROOT / ".tmp/docker-integration/secrets.json").read_text())
    variables = {
        "assignee": realm["users"]["outsider"],
        "required_quantity": float(order["lines"][0]["quantity"]),
        "data_request": {
            "product_key": "warehouse.stock_availability",
            "contract_version": "1.0.0",
            "columns": ["org_id", "warehouse_id", "product_id", "sku", "available_quantity"],
            "filters": {
                "org_id": live.resources["org_id"],
                "warehouse_id": live.resources["warehouse_id"],
                "product_id": order["lines"][0]["product_ref"],
            },
        },
        "custom_request": ref,
        "action_requests": {
            "shipment_request": ref,
            **{
                f"sales_{key}_request": {**ref, "status": status}
                for key, status in (
                    ("reserved", "RESERVED"),
                    ("shipped", "SHIPPED"),
                    ("delivered", "DELIVERED"),
                    ("no_stock", "WAITING_STOCK"),
                )
            },
        },
    }
    key = uuid4().hex
    response = live.clients["sales"].post(
        "/api/v1/workflows/order_fulfillment/start",
        headers={"Idempotency-Key": key},
        json={"business_key": order["id"], "variables": variables},
    )
    assert response.status_code == 201, f"Start: {response.status_code} {response.text}"
    replay = live.clients["sales"].post(
        "/api/v1/workflows/order_fulfillment/start",
        headers={"Idempotency-Key": key},
        json={"business_key": order["id"], "variables": variables},
    )
    assert (
        replay.status_code == 201 and replay.json()["instance_id"] == response.json()["instance_id"]
    )
    return response.json()["instance_id"]


def test_live_business_workflow_to_end_with_real_hasura_event(live):
    state = live.resources
    order = create_order(state, live.auth, "flow", (4, 2))
    instance = start(live, order)
    task = engine_request(live, "GET", f"/runtime/tasks?processInstanceId={instance}")["data"][0]
    ref = {"org_id": state["org_id"], "order_id": order["id"]}
    forbidden = live.clients["it"].post(
        "/api/v1/workflows/tasks/" + task["id"] + "/complete", json={"variables": {"packed": True}}
    )
    assert forbidden.status_code == 403
    bound = request(
        "sales",
        "POST",
        "/orders/status",
        live.auth,
        body={**ref, "status": "RESERVED", "workflow_instance_ref": instance},
    )
    assert bound["workflow_instance_ref"] == instance
    live.restart("api")
    compose("restart", "flowable")
    deadline = time.monotonic() + 90
    while True:
        try:
            restored = engine_request(live, "GET", "/runtime/tasks/" + task["id"])
            break
        except (httpx.HTTPError, AssertionError):
            assert time.monotonic() < deadline, "Flowable restart did not restore user task"
            time.sleep(1)
    assert restored["assignee"] == task["assignee"]
    status = live.clients["sales"].get("/api/v1/workflows/instances/" + instance)
    assert status.status_code == 200 and not status.json()["completed"]
    # Missing physical packing cannot be hidden by completing the BPMN user task.
    unpacked = live.clients["outsider"].post(
        "/api/v1/workflows/tasks/" + task["id"] + "/complete", json={"variables": {"packed": True}}
    )
    assert unpacked.status_code >= 400
    assert engine_request(live, "GET", "/runtime/tasks/" + task["id"])["id"] == task["id"]
    request("warehouse", "POST", "/pack", live.auth, label="outsider", body=ref)
    live.business.stop("logistics")
    unavailable = live.clients["outsider"].post(
        "/api/v1/workflows/tasks/" + task["id"] + "/complete", json={"variables": {"packed": True}}
    )
    assert unavailable.status_code >= 400
    assert engine_request(live, "GET", "/runtime/tasks/" + task["id"])["id"] == task["id"]
    live.business.start("logistics")
    complete = live.clients["outsider"].post(
        "/api/v1/workflows/tasks/" + task["id"] + "/complete", json={"variables": {"packed": True}}
    )
    assert complete.status_code == 200, f"User task {complete.status_code} {complete.text}"
    shipment = request(
        "logistics", "GET", f"/shipments/by-order/{order['id']}?org_id={state['org_id']}", live.auth
    )
    assert shipment["status"] == "CREATED"
    wrong = {
        "event_id": str(uuid4()),
        "event_type": "shipment.delivered",
        "payload": {"order_id": str(uuid4()), "status": "DELIVERED"},
    }
    raw = json.dumps(wrong, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(live.secrets["EVENT_HMAC_SECRET"].encode(), raw, hashlib.sha256).hexdigest()
    response = httpx.post(
        live.base_url + "/api/v1/workflows/events",
        content=raw,
        headers={"X-Event-Signature": sig, "Content-Type": "application/json"},
    )
    assert response.status_code == 404
    assert engine_request(live, "GET", "/runtime/process-instances/" + instance)["id"] == instance
    request("warehouse", "POST", "/handoff", live.auth, label="outsider", body=ref)
    request(
        "logistics",
        "POST",
        "/shipments/status",
        live.auth,
        label="it",
        body={**ref, "status": "HANDED_OVER"},
    )
    request(
        "logistics",
        "POST",
        "/shipments/status",
        live.auth,
        label="it",
        body={**ref, "status": "DELIVERED"},
    )
    final = wait_until(
        lambda: (
            r
            if (
                r := request(
                    "sales", "GET", f"/orders/{order['id']}?org_id={state['org_id']}", live.auth
                )
            )["status"]
            == "DELIVERED"
            else None
        )
    )
    history = engine_request(live, "GET", f"/history/historic-process-instances/{instance}")
    assert history["endTime"] and final["last_source_event_ref"] == shipment["event_id"]
    live.evidence(
        "business_end",
        {
            "instance_id": instance,
            "order_id": order["id"],
            "shipment_id": shipment["id"],
            "status": final["status"],
        },
    )
    live.evidence(
        "restart_and_failures",
        "PASS: task persisted; unpacked/API-down/other-user/wrong-order did not advance",
    )
    events = []
    for domain in ("sales", "logistics"):
        from scripts.database.config import database_url

        engine = sa.create_engine(
            database_url("admin").set(database=domain + "_db"), hide_parameters=True
        )
        try:
            with engine.connect() as conn:
                captured = (
                    conn.execute(
                        sa.text("""SELECT e.id,e.delivered,l.request::jsonb->'payload' AS body
                    FROM hdb_catalog.event_log e JOIN LATERAL (
                        SELECT request FROM hdb_catalog.event_invocation_logs l
                        WHERE l.event_id=e.id ORDER BY created_at DESC LIMIT 1
                    ) l ON true WHERE e.trigger_name=:trigger"""),
                        {"trigger": state["run_id"] + "_" + domain},
                    )
                    .mappings()
                    .all()
                )
                assert all(e["delivered"] for e in captured)
                if domain == "sales":
                    assert len(captured) == 1  # Status updates cannot self-trigger INSERT.
                events.extend(
                    (domain, dict(e))
                    for e in captured
                    if e["body"]["event"]["data"]["new"]["status"] in {"CONFIRMED", "DELIVERED"}
                )
        finally:
            engine.dispose()
    live.restart("api")
    for domain, event in events:
        response = httpx.post(
            live.base_url + "/api/v1/workflows/hasura-events",
            json=event["body"],
            headers={
                "X-Hasura-Event-Secret": live.secrets["RELAY_HEADER_SECRET"],
                "X-FlowBridge-Source": f"wf_e2e_{domain}_events",
            },
            timeout=30,
        )
        assert response.status_code == 200, response.text
        assert response.json()["outcomes"][0]["deduped"] is True
    live.evidence(
        "hasura_start_correlate_replay",
        {
            "event_ids": [e["id"] for _, e in events],
            "result": "PASS: actual INSERT START, UPDATE CORRELATE, duplicate replay after restart",
        },
    )


def test_live_shortage_ends_without_reservation_or_success(live):
    state = live.resources
    order = create_order(state, live.auth, "no_stock", (9999, 1))
    instance = start(live, order)
    current = request("sales", "GET", f"/orders/{order['id']}?org_id={state['org_id']}", live.auth)
    assert current["status"] == "WAITING_STOCK"
    assert engine_request(live, "GET", f"/history/historic-process-instances/{instance}")["endTime"]
    engine = make_engine("warehouse")
    try:
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM reservations WHERE sales_order_ref=:order"),
                    {"order": UUID(order["id"])},
                )
                == 0
            )
    finally:
        engine.dispose()
    live.evidence(
        "insufficient", {"instance_id": instance, "status": "WAITING_STOCK", "reservations": 0}
    )


def test_live_data_action_permissions_and_late_event_guards(live):
    state = live.resources
    payload = {
        "product_key": "warehouse.stock_availability",
        "contract_version": "1.0.0",
        "filters": {
            "org_id": state["org_id"],
            "warehouse_id": state["warehouse_id"],
            "product_id": state["products"][0],
        },
        "columns": ["org_id", "warehouse_id", "product_id", "sku", "available_quantity"],
    }
    path = "/api/v1/workflows/actions/data.get_record/execute"
    assert live.clients["it"].post(path, json=payload).status_code == 403
    assert (
        live.clients["sales"]
        .post(path, json={**payload, "columns": [*payload["columns"], "unit_cost"]})
        .status_code
        == 422
    )
    before = request(
        "warehouse",
        "GET",
        f"/inventory?org_id={state['org_id']}&warehouse_id={state['warehouse_id']}",
        live.auth,
        label="outsider",
    )
    unavailable = live.clients["sales"].post(
        "/api/v1/workflows/actions/data.update/execute",
        json={
            "product_key": payload["product_key"],
            "contract_version": "1.0.0",
            "filters": payload["filters"],
            "patch": {"available_quantity": 999999},
        },
    )
    assert unavailable.status_code == 409  # Explicitly unavailable; not a Hasura write acceptance.
    assert (
        request(
            "warehouse",
            "GET",
            f"/inventory?org_id={state['org_id']}&warehouse_id={state['warehouse_id']}",
            live.auth,
            label="outsider",
        )
        == before
    )
    assert (
        httpx.post(
            live.base_url + "/api/v1/workflows/hasura-events",
            json={},
            headers={"X-Hasura-Event-Secret": "wrong"},
        ).status_code
        == 401
    )
    assert httpx.post(live.base_url + "/api/v1/workflows/events", json={}).status_code == 401
    from scripts.database.config import database_url

    engine = sa.create_engine(
        database_url("admin").set(database="logistics_db"), hide_parameters=True
    )
    try:
        with engine.connect() as conn:
            body = conn.scalar(
                sa.text("""SELECT l.request::jsonb->'payload'
                FROM hdb_catalog.event_invocation_logs l
                JOIN hdb_catalog.event_log e ON e.id=l.event_id
                WHERE e.trigger_name=:name AND e.payload->'data'->'new'->>'status'='DELIVERED'
                ORDER BY l.created_at DESC LIMIT 1"""),
                {"name": state["run_id"] + "_logistics"},
            )
    finally:
        engine.dispose()
    body["id"] = str(uuid4())
    body["event"]["data"]["new"]["version"] = 0
    response = httpx.post(
        live.base_url + "/api/v1/workflows/hasura-events",
        json=body,
        headers={
            "X-Hasura-Event-Secret": live.secrets["RELAY_HEADER_SECRET"],
            "X-FlowBridge-Source": "wf_e2e_logistics_events",
        },
    )
    assert response.status_code == 200 and response.json() == {"ignored": True}
    # The actual INSERT webhook started a separate engine process, independently
    # of the manually authorized main workflow start.
    core = make_engine("core")
    try:
        with core.connect() as conn:
            outcomes = conn.scalars(
                sa.text(
                    "SELECT result_json FROM workflow_event_inbox "
                    "WHERE result_json->>'subscription_id'=:sub"
                ),
                {"sub": state["run_id"] + "_start"},
            ).all()
            assert outcomes
    finally:
        core.dispose()
    for outcome in outcomes:
        history = engine_request(
            live, "GET", "/history/historic-process-instances/" + outcome["instance_id"]
        )
        assert history["endTime"] and history["processDefinitionId"].startswith(
            "wf_e2e_event_start:"
        )
    live.evidence(
        "event_security_and_data_permissions",
        "PASS: real START history, stale event ignored; invalid header/HMAC, "
        "private column and no-grant denied; data.update remains unavailable",
    )
