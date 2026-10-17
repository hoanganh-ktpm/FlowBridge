# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
import os
import re
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from flowbridge.shared.errors import ErrorResponse

pytestmark = pytest.mark.integration
CONFIG = "/api/v1/core/identity/config"
ME = "/api/v1/core/identity/me"


def test_only_registered_methods_and_paths_reach_identity(gateway: httpx.Client) -> None:
    response = gateway.get(CONFIG)
    assert response.status_code == 200
    assert response.json()["pkce_required"] is True
    for path in (ME,):
        rejected = gateway.get(path)
        assert rejected.status_code == 401
        error = ErrorResponse.model_validate(rejected.json())
        assert error.request_id == rejected.headers["x-correlation-id"]
        assert gateway.get(path, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert gateway.post(CONFIG).status_code == 404
    assert gateway.get("/api/v1/core/identity/unknown").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/internal/v1/core/data-schemas",
        "/admin/v1/users",
        "/apisix/admin/routes",
        "/_ops/ready",
        "/api/v1/me",
        "/docs",
        "/openapi.json",
        "/%69nternal/v1/core/data-schemas",
        "//internal/v1/core/data-schemas",
        "/api/v1/core/identity/%2e%2e/%2e%2e/%2e%2e/internal/v1/core/data-schemas",
        "/internal%2fv1%2fcore%2fdata-schemas",
    ],
)
def test_public_listener_has_no_private_or_admin_route(gateway: httpx.Client, path: str) -> None:
    response = gateway.get(path)
    assert response.status_code == 404


def test_external_network_cannot_resolve_receiver_or_reach_admin(gateway: httpx.Client) -> None:
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("identity", 18092)
    with socket.socket() as connection:
        connection.settimeout(2)
        assert (
            connection.connect_ex(
                (
                    os.environ["FLOWBRIDGE_GATEWAY_RECEIVER_IP"],
                    int(os.environ["FLOWBRIDGE_GATEWAY_RECEIVER_PORT"]),
                )
            )
            != 0
        )
    with socket.socket() as connection:
        connection.settimeout(2)
        assert connection.connect_ex((socket.gethostbyname("gateway"), 9180)) != 0
    assert gateway.get(CONFIG).status_code == 200


def test_tls_requires_the_profile_ca(gateway: httpx.Client) -> None:
    with httpx.Client(verify=ssl.create_default_context(), trust_env=False) as untrusted:
        with pytest.raises(httpx.ConnectError):
            untrusted.get("https://gateway:9443" + CONFIG)
    assert gateway.get(CONFIG).status_code == 200


def test_cors_uses_explicit_origins_and_allows_preflight(gateway: httpx.Client) -> None:
    allowed = gateway.options(
        ME,
        headers={
            "Origin": "http://127.0.0.1:8761",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization,X-Correlation-Id",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:8761"
    denied = gateway.get(CONFIG, headers={"Origin": "https://attacker.invalid"})
    assert "access-control-allow-origin" not in denied.headers


@pytest.mark.parametrize("value", ["valid_trace-01", "bad value", "x" * 129])
def test_correlation_is_consistent_on_success_and_auth_error(
    gateway: httpx.Client,
    value: str,
) -> None:
    for path, status in ((CONFIG, 200), (ME, 401)):
        response = gateway.get(path, headers={"X-Correlation-Id": value})
        assert response.status_code == status
        trace = response.headers["x-correlation-id"]
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", trace)
        if value == "valid_trace-01":
            assert trace == value
        else:
            assert trace != value


def test_oversized_request_is_rejected_before_upstream(gateway: httpx.Client) -> None:
    response = gateway.request("GET", CONFIG, content=b"x" * 2048)
    assert response.status_code == 413
    error = ErrorResponse.model_validate(response.json())
    assert error.request_id == response.headers["x-correlation-id"]


def test_real_proxy_timeout_uses_core_error_envelope(gateway: httpx.Client) -> None:
    response = gateway.get("https://gateway-timeout:9443" + CONFIG)
    assert response.status_code == 504
    error = ErrorResponse.model_validate(response.json())
    assert error.code.value == "UPSTREAM_TIMEOUT"
    assert error.request_id == response.headers["x-correlation-id"]


def test_z_burst_is_bounded_by_gateway(gateway: httpx.Client) -> None:
    with ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(lambda _: gateway.get(CONFIG).status_code, range(80)))
    assert 429 in statuses
    assert set(statuses) <= {200, 429}
