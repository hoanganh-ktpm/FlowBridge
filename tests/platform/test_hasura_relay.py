import hashlib
import hmac
import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest

from app.platform.http.errors import CoreError, ErrorCode
from app.platform.workflow.flowable import FlowableClient
from app.platform.workflow.relay import forward_hasura, normalize_hasura_event
from app.platform.workflow.settings import WorkflowApiSettings


@pytest.fixture
def envelope():
    org, order = str(uuid4()), str(uuid4())
    body = {
        "id": str(uuid4()),
        "trigger": {"name": "delivery"},
        "table": {"schema": "public", "name": "wf_e2e_shipments"},
        "event": {
            "op": "UPDATE",
            "data": {
                "old": {"status": "HANDED_OVER", "version": 2},
                "new": {
                    "org_id": org,
                    "sales_order_ref": order,
                    "status": "DELIVERED",
                    "version": 3,
                },
            },
        },
    }
    rules = {
        "delivery": {
            "source": "logistics-events",
            "schema": "public",
            "table": "wf_e2e_shipments",
            "operation": "UPDATE",
            "org_ids": [org],
            "status": "DELIVERED",
            "order_field": "sales_order_ref",
            "event_type": "shipment.delivered",
        }
    }
    return body, rules


@pytest.mark.parametrize("mode", ["old", "same", "loop"])
def test_relay_ignores_old_same_state_and_self_trigger(envelope, mode):
    body, rules = deepcopy(envelope)
    row = body["event"]["data"]["new"]
    if mode == "old":
        row["version"] = 1
    if mode == "same":
        body["event"]["data"]["old"]["status"] = "DELIVERED"
    if mode == "loop":
        row["triggered_by"] = "workflow"
    assert normalize_hasura_event(body, rules, "logistics-events") is None


@pytest.mark.asyncio
async def test_relay_authenticates_then_signs_actual_normalized_bytes(envelope):
    body, rules = envelope
    settings = WorkflowApiSettings(
        _env_file=None,
        hasura_relay_header_secret="trusted-hasura-header",
        event_webhook_secret="body-hmac-secret",
        hasura_relay_bindings=rules,
    )
    calls = []

    def upstream(request):
        calls.append(request)
        signature = hmac.new(b"body-hmac-secret", request.content, hashlib.sha256).hexdigest()
        assert request.headers["X-Event-Signature"] == signature
        assert request.headers["X-Event-Signature"] != "trusted-hasura-header"
        event = json.loads(request.content)
        assert set(event["payload"]) == {"order_id", "status"}
        return httpx.Response(202, json={"outcomes": [{"instance_id": "real-boundary-double"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        raw = json.dumps(body).encode()
        with pytest.raises(CoreError) as denied:
            await forward_hasura(raw, "wrong", settings, http, "logistics-events")
        assert denied.value.code == ErrorCode.UNAUTHENTICATED and not calls
        await forward_hasura(raw, "trusted-hasura-header", settings, http, "logistics-events")
        assert len(calls) == 1


def test_relay_refuses_unconfigured_source_or_org(envelope):
    body, rules = envelope
    with pytest.raises(CoreError):
        normalize_hasura_event(body, rules, "wrong-source")
    body["event"]["data"]["new"]["org_id"] = str(uuid4())
    with pytest.raises(CoreError):
        normalize_hasura_event(body, rules, "logistics-events")


@pytest.mark.asyncio
async def test_correlation_rejects_engine_ignoring_business_key_filter():
    writes = []

    def upstream(request):
        if request.method == "PUT":
            writes.append(request)
        return httpx.Response(
            200, json={"data": [{"id": "unrelated", "businessKey": "other-order"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        with pytest.raises(CoreError) as rejected:
            await FlowableClient(
                http, "http://127.0.0.1:18083/service", "test", "private"
            ).message_event("delivered", "expected-order", {}, workflow_key="test")
    assert rejected.value.code == ErrorCode.UPSTREAM_UNAVAILABLE and not writes
