# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
import importlib.util
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from flowbridge.identity.router import router
from flowbridge.settings import Settings
from flowbridge.shared.access_log import SafeAccessFilter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare, stack_module = load("prepare_gateway"), load("gateway_stack")


def test_access_logging_never_retains_query_credentials() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        "%s %s %s %s %s",
        ("127.0.0.1:1234", "GET", "/api/v1/core/identity/config?token=secret-value", "1.1", 200),
        None,
    )
    assert SafeAccessFilter().filter(record)
    assert "secret-value" not in record.getMessage()
    assert "?" not in record.getMessage()
    assert record.correlation_id == "unassigned"


def test_access_logger_rejects_unexpected_record_shapes() -> None:
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "raw %s", ("secret",), None
    )
    assert not SafeAccessFilter().filter(record)


def test_gateway_allowlist_matches_implemented_identity_contract() -> None:
    config = yaml.safe_load((ROOT / "infra/gateway/routes.yaml").read_text("utf-8"))
    actual = {
        (method, item["uri"])
        for item in config["routes"]
        for method in item["methods"]
        if method != "OPTIONS"
    }
    expected = {(method, route.path) for route in router.routes for method in route.methods}
    assert actual == expected
    assert all("*" not in path and method == "GET" for method, path in actual)


@pytest.mark.parametrize("value", [["*"], ["0.0.0.0/0"], ["::/0"], ["gateway"], ["127.0.0.1,evil"]])
def test_proxy_trust_cannot_be_unbounded_or_arbitrary_host(value: list[str]) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, trusted_proxy_ips=value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peer,expected", [("172.29.240.2", "203.0.113.10"), ("172.29.240.3", "172.29.240.3")]
)
async def test_forwarding_metadata_is_used_only_from_the_explicit_proxy(
    peer: str,
    expected: str,
) -> None:
    seen = {}

    async def receiver(scope: dict, receive: object, send: object) -> None:
        seen.update(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    settings = Settings(_env_file=None, trusted_proxy_ips=["172.29.240.2"])
    app = ProxyHeadersMiddleware(receiver, trusted_hosts=settings.trusted_proxy_ips)
    transport = httpx.ASGITransport(app, client=(peer, 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://receiver") as client:
        response = await client.get(
            "/",
            headers={
                "X-Forwarded-For": "203.0.113.10",
                "X-Forwarded-Proto": "https",
            },
        )
    assert response.status_code == 204
    assert seen["client"][0] == expected
    assert seen["scheme"] == ("https" if peer == "172.29.240.2" else "http")


@pytest.mark.parametrize("failure", ["browser", "external", "revision"])
def test_gateway_acceptance_failure_is_not_hidden_and_fixture_is_cleaned(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    stack = stack_module.GatewayStack(
        "flowbridge-gateway-test-abcdef012345",
        ROOT / ".env.example",
        18092,
        19454,
        test_mode=True,
    )
    commands, phases = [], []
    monkeypatch.setattr(stack, "has_volumes", lambda: False)
    monkeypatch.setattr(stack, "save_run", phases.append)
    monkeypatch.setattr(stack, "receiver_address", lambda: "172.29.240.129")

    def run(*args: str, **kwargs: object) -> object:
        commands.append(args)
        if (args[0] == "run" and args[-1] == failure) or (
            failure == "revision" and "--require-head" in args
        ):
            raise stack_module.StackError("forced failure")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(stack, "run", run)
    with pytest.raises(stack_module.StackError, match="forced failure"):
        stack.acceptance()
    assert commands[-1] == ("down", "--volumes")
    assert any(command[-1] == "cleanup" for command in commands) == (failure != "revision")
    assert phases[-1] == "gateway-failed-cleaned"


def test_gateway_recovery_retains_state_if_fixture_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = stack_module.GatewayStack(
        "flowbridge-gateway-test-abcdef012345",
        ROOT / ".env.example",
        18092,
        19454,
        test_mode=True,
    )
    commands = []
    monkeypatch.setattr(stack, "has_volumes", lambda: False)
    monkeypatch.setattr(stack, "save_run", lambda phase: None)
    monkeypatch.setattr(stack, "receiver_address", lambda: "172.29.240.129")

    def run(*args: str, **kwargs: object) -> object:
        commands.append(args)
        if args[0] == "run" and args[-1] == "browser":
            raise stack_module.StackError("forced browser failure")
        return SimpleNamespace(returncode=1 if args[-1] == "cleanup" else 0, stdout="")

    monkeypatch.setattr(stack, "run", run)
    with pytest.raises(stack_module.StackError, match="preserve volumes"):
        stack.acceptance()
    assert commands[-1] == ("down",)
    assert not any("--volumes" in command for command in commands)
