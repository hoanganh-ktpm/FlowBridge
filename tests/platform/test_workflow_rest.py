"""Offline HTTP adapter tests, not evidence of a running Flowable engine."""

import json

import httpx
import pytest

from app.platform.http.errors import CoreError, ErrorCode
from app.platform.workflow.actions import ActionDefinition, ActionRegistry
from app.platform.workflow.flowable import FlowableClient
from app.platform.workflow.gateway import ActionGateway, AllowlistHttpPort

pytestmark = pytest.mark.asyncio


async def test_custom_action_rejects_remote_schema_references():
    registry = ActionRegistry()
    with pytest.raises(CoreError) as error:
        registry.register(
            ActionDefinition(
                "remote.schema",
                1,
                "warehouse",
                "HTTP",
                {"$ref": "https://untrusted.test/schema"},
                {"type": "object"},
                service_ref="warehouse",
                method="POST",
                path="/call",
            )
        )
    assert error.value.code == ErrorCode.VALIDATION_ERROR


async def test_message_correlation_uses_exact_execution_and_workflow_key():
    requests = []

    def upstream(request):
        requests.append(request)
        assert request.headers["Authorization"].startswith("Basic ")
        if request.url.path.endswith("/query/process-instances"):
            assert json.loads(request.content)["processDefinitionKey"] == "shipping"
            assert json.loads(request.content)["processBusinessKey"] == "order-1"
            assert "businessKey" not in json.loads(request.content)
            assert request.url.params["size"] == "100"
            return httpx.Response(
                200, json={"data": [{"id": "run-1", "businessKey": "order-1"}], "total": 1}
            )
        if request.url.path.endswith("/runtime/executions"):
            assert request.url.params["processInstanceId"] == "run-1"
            assert request.url.params["messageEventSubscriptionName"] == "delivered"
            return httpx.Response(200, json={"data": [{"id": "execution-1"}], "total": 1})
        assert request.method == "PUT"
        assert request.url.path.endswith("/runtime/executions/execution-1")
        assert json.loads(request.content) == {
            "action": "messageEventReceived",
            "messageName": "delivered",
            "variables": [{"name": "status", "value": "done"}],
        }
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        client = FlowableClient(
            http, "http://127.0.0.1:18081/flowable-rest/service", "user", "secret"
        )
        result = await client.message_event(
            "delivered", "order-1", {"status": "done"}, workflow_key="shipping"
        )
        assert result == {"id": "run-1"}
        assert len(requests) == 3


async def test_message_correlation_refuses_ambiguous_subscriptions():
    writes = []

    def upstream(request):
        if request.method == "PUT":
            writes.append(request)
        if request.url.path.endswith("/query/process-instances"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "run-1", "businessKey": "order-1"},
                        {"id": "run-2", "businessKey": "order-1"},
                    ]
                },
            )
        return httpx.Response(200, json={"data": [{"id": "execution-1"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        client = FlowableClient(http, "https://engine.test/service", "user", "secret")
        with pytest.raises(CoreError) as error:
            await client.message_event("delivered", "order-1", {}, workflow_key="shipping")
        assert error.value.code == ErrorCode.VERSION_CONFLICT
        assert writes == []


async def test_completed_instance_uses_history_without_exposing_upstream_errors():
    def upstream(request):
        if "/history/" in request.url.path:
            return httpx.Response(200, json={"id": "run-1", "endTime": "2026-10-24T00:00:00Z"})
        return httpx.Response(404, json={"private": "must-not-leak"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        client = FlowableClient(http, "https://engine.test/service", "user", "secret")
        assert (await client.get_instance("run-1"))["completed"] is True
        with pytest.raises(CoreError) as error:
            await client.get_task("../other")
        assert error.value.code == ErrorCode.VALIDATION_ERROR


async def test_rest_transport_errors_are_sanitized():
    def upstream(request):
        raise httpx.ReadTimeout("private diagnostic including credentials")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        client = FlowableClient(http, "https://engine.test/service", "user", "secret")
        with pytest.raises(CoreError) as error:
            await client.get_task("task-1")
        assert error.value.code == ErrorCode.UPSTREAM_TIMEOUT
        assert "credentials" not in str(error.value)
        assert "secret" not in repr(client)


async def test_registered_http_action_uses_per_call_tokens_and_contracts():
    registry = ActionRegistry()
    registry.register(
        ActionDefinition(
            "warehouse.reserve",
            1,
            "warehouse",
            "HTTP",
            {"type": "object"},
            {"type": "object", "required": ["reserved"]},
            service_ref="warehouse",
            method="POST",
            path="/reservations",
        )
    )
    tokens = []

    def upstream(request):
        tokens.append(request.headers.get("Authorization"))
        assert request.headers["Idempotency-Key"] == "command-1"
        return httpx.Response(200, json={"reserved": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        gateway = ActionGateway(
            registry,
            http_port=AllowlistHttpPort(http, frozenset({"warehouse.test"})),
            service_bases={"warehouse": "https://warehouse.test"},
        )
        for token in ("caller-a", "caller-b"):
            assert await gateway.execute(
                "warehouse.reserve", {}, token=token, idempotency_key="command-1"
            ) == {"reserved": True}
        assert tokens == ["Bearer caller-a", "Bearer caller-b"]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/reserve",
        "http://warehouse.test/reserve",
        "https://secret@warehouse.test/reserve",
    ],
)
async def test_custom_http_destination_must_be_allowlisted(url):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("Forbidden destination was contacted")
        )
    ) as http:
        port = AllowlistHttpPort(http, frozenset({"warehouse.test"}))
        with pytest.raises(CoreError) as error:
            await port.call("POST", url, {}, {})
        assert error.value.code == ErrorCode.FORBIDDEN


async def test_custom_action_timeout_has_public_error_envelope():
    import asyncio

    registry = ActionRegistry()
    registry.register(
        ActionDefinition(
            "slow.action",
            1,
            "warehouse",
            "HTTP",
            {"type": "object"},
            {"type": "object"},
            service_ref="warehouse",
            method="POST",
            path="/slow",
            timeout_seconds=0.01,
        )
    )

    async def upstream(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        gateway = ActionGateway(
            registry,
            http_port=AllowlistHttpPort(http, frozenset({"warehouse.test"})),
            service_bases={"warehouse": "https://warehouse.test"},
        )
        with pytest.raises(CoreError) as error:
            await gateway.execute("slow.action", {})
        assert error.value.code == ErrorCode.UPSTREAM_TIMEOUT
