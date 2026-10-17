# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Supported Admin API boundary; these mocks are not evidence of real SSO.
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from flowbridge.identity.directory import KeycloakDirectory
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["human", "service"])
async def test_directory_derives_identity_type_from_provider(kind: str) -> None:
    subject = str(uuid4())
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/token"):
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "directory-only-secret-token"})
        if request.url.path.endswith("/clients"):
            return httpx.Response(
                200,
                json=[{"id": "11111111-1111-4111-8111-111111111111", "clientId": "flowbridge-ai"}],
            )
        if request.url.path.endswith("/service-account-user"):
            return httpx.Response(200, json={"id": subject if kind == "service" else str(uuid4())})
        assert request.url.path.endswith("/users/" + subject)
        assert request.headers["authorization"] == "Bearer directory-only-secret-token"
        payload = {"id": subject, "username": "account", "enabled": True}
        if kind == "service":
            payload["serviceAccountClientId"] = "flowbridge-ai"
        return httpx.Response(200, json=payload)

    settings = Settings(
        _env_file=None,
        oidc_issuer="https://sso.invalid/realms/flowbridge",
        oidc_admin_url="https://sso.invalid/admin/realms/flowbridge",
        oidc_admin_client_id="directory",
        oidc_admin_client_secret="test-secret",
        oidc_service_clients=["flowbridge-ai"],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        result = await KeycloakDirectory(http, settings).resolve(subject)
    assert len(calls) == 4
    assert result.subject == subject and result.account_type == kind
    assert result.issuer == settings.oidc_issuer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,expected",
    [
        ("missing", ErrorCode.NOT_FOUND),
        ("disabled", ErrorCode.FORBIDDEN),
        ("wrong_id", ErrorCode.UPSTREAM_UNAVAILABLE),
        ("unknown_service", ErrorCode.FORBIDDEN),
        ("bad_json", ErrorCode.UPSTREAM_UNAVAILABLE),
        ("timeout", ErrorCode.UPSTREAM_TIMEOUT),
        ("denied", ErrorCode.UPSTREAM_UNAVAILABLE),
    ],
)
async def test_directory_errors_fail_closed(failure: str, expected: ErrorCode) -> None:
    subject = str(uuid4())

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            if failure == "timeout":
                raise httpx.ReadTimeout("must-never-expose-token")
            if failure == "denied":
                return httpx.Response(401)
            if failure == "bad_json":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"access_token": "token"})
        if failure == "missing":
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "id": "wrong" if failure == "wrong_id" else subject,
                "username": "account",
                "enabled": failure != "disabled",
                **({"serviceAccountClientId": "unknown"} if failure == "unknown_service" else {}),
            },
        )

    settings = Settings(
        _env_file=None,
        oidc_issuer="https://sso.invalid/realms/flowbridge",
        oidc_admin_url="https://sso.invalid/admin/realms/flowbridge",
        oidc_admin_client_id="directory",
        oidc_admin_client_secret="secret",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        with pytest.raises(CoreError) as error:
            await KeycloakDirectory(http, settings).resolve(subject)
    assert error.value.code == expected
    assert "must-never-expose-token" not in str(error.value)


@pytest.mark.parametrize("origin", ["*", "https://host/", "https://u:p@host", "http://host/path"])
def test_cors_requires_explicit_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=[origin])


def test_blank_identity_ca_and_secrets_leave_provider_unconfigured() -> None:
    settings = Settings(
        _env_file=None, oidc_ca_bundle="", oidc_client_secret="", oidc_admin_client_secret=""
    )
    assert settings.oidc_ca_bundle is None
    assert settings.oidc_client_secret is settings.oidc_admin_client_secret is None
