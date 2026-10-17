# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Exercise the upstream introspection boundary without claiming a real IdP integration.
from collections.abc import AsyncIterator
from dataclasses import dataclass
from time import time
from typing import Annotated

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI

from flowbridge.identity.authentication import VerifiedIdentity, authenticated_identity
from flowbridge.settings import Settings
from flowbridge.shared.correlation import CorrelationMiddleware
from flowbridge.shared.errors import register_exception_handlers


@dataclass
class AuthResources:
    http: httpx.AsyncClient


@pytest_asyncio.fixture
async def auth_client() -> AsyncIterator[tuple[httpx.AsyncClient, dict]]:
    claims = {
        "active": True,
        "iss": "https://identity.example.invalid/realm",
        "sub": "employee",
        "aud": ["flowbridge"],
        "exp": time() + 300,
        "token_type": "Bearer",
        "client_id": "flowbridge-web-a",
    }

    def introspect(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/introspect"
        assert request.headers["authorization"].startswith("Basic ")
        assert b"token=test-access-token" in request.content
        return httpx.Response(200, json=claims)

    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(CorrelationMiddleware)
    app.state.settings = Settings(
        _env_file=None,
        oidc_issuer="https://identity.example.invalid/realm",
        oidc_audience="flowbridge",
        oidc_introspection_url="https://identity.example.invalid/introspect",
        oidc_client_id="backend",
        oidc_client_secret="only-a-test-secret",
    )

    @app.get("/identity")
    async def identity(
        principal: Annotated[VerifiedIdentity, Depends(authenticated_identity)],
    ) -> dict:
        return {"issuer": principal.issuer, "subject": principal.subject}

    async with httpx.AsyncClient(transport=httpx.MockTransport(introspect)) as upstream:
        app.state.resources = AuthResources(upstream)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, claims


@pytest.mark.asyncio
async def test_no_bearer_never_accepts_user_or_role_headers(auth_client: tuple) -> None:
    client, _ = auth_client
    response = await client.get("/identity", headers={"X-User-Id": "admin", "X-Role": "ADMIN"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_active_token_is_mapped_by_exact_issuer_and_subject(auth_client: tuple) -> None:
    client, _ = auth_client
    response = await client.get("/identity", headers={"Authorization": "Bearer test-access-token"})
    assert response.status_code == 200
    assert response.json() == {
        "issuer": "https://identity.example.invalid/realm",
        "subject": "employee",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"active": False},
        {"iss": "https://attacker.invalid"},
        {"sub": ""},
        {"aud": ["other-api"]},
        {"exp": 0},
        {"exp": None},
        {"active": "true"},
        {"token_type": "Refresh"},
        {"typ": "Refresh"},
        {"token_type": "ID"},
        {"client_id": None},
        {"azp": "other-client"},
        {"nbf": 99999999999},
        {"exp": True},
        {"nbf": True},
    ],
)
async def test_inactive_wrong_audience_issuer_or_expired_token_denied(
    auth_client: tuple, updates: dict
) -> None:
    client, claims = auth_client
    claims.update(updates)
    response = await client.get("/identity", headers={"Authorization": "Bearer test-access-token"})
    assert response.status_code == 401
    assert "test-access-token" not in response.text and "only-a-test-secret" not in response.text
