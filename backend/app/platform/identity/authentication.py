# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Delegate token validation to the configured authorization server (RFC 7662).
from dataclasses import dataclass
from math import isfinite
from time import time
from typing import Annotated, Literal

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.platform.http.errors import CoreError, ErrorCode

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    client_id: str | None = None
    account_type: Literal["human", "service"] = "human"
    scopes: frozenset[str] = frozenset()


async def authenticated_identity(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> VerifiedIdentity:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or len(request.headers.getlist("authorization")) != 1
    ):
        raise CoreError(ErrorCode.UNAUTHENTICATED)
    return await verify_access_token(request, credentials.credentials)


async def verify_access_token(request: Request, token: str) -> VerifiedIdentity:
    # Also used for an IAM-issued delegated user token, with the same upstream checks.
    settings = request.app.state.settings
    if not all(
        (
            settings.oidc_issuer,
            settings.oidc_audience,
            settings.oidc_introspection_url,
            settings.oidc_client_id,
            settings.oidc_client_secret,
        )
    ):
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    resources = request.app.state.resources
    if resources is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    try:
        response = await resources.http.post(
            settings.oidc_introspection_url,
            data={"token": token, "token_type_hint": "access_token"},
            auth=httpx.BasicAuth(
                settings.oidc_client_id, settings.oidc_client_secret.get_secret_value()
            ),
        )
        response.raise_for_status()
        claims = response.json()
    except httpx.TimeoutException:
        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
    except (httpx.HTTPError, ValueError):
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
    if not isinstance(claims, dict):
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    audiences = claims.get("aud", [])
    audiences = [audiences] if isinstance(audiences, str) else audiences
    expiry = claims.get("exp")
    client_id = claims.get("client_id")
    not_before = claims.get("nbf", 0)
    scope = claims.get("scope", "")
    if (
        claims.get("active") is not True
        or claims.get("iss") != settings.oidc_issuer
        or not isinstance(claims.get("sub"), str)
        or not claims["sub"].strip()
        or not isinstance(audiences, list)
        or settings.oidc_audience not in audiences
        or not isinstance(expiry, (float, int))
        or isinstance(expiry, bool)
        or not isfinite(expiry)
        or expiry <= time()
        or claims.get("token_type") != "Bearer"
        or claims.get("typ", "Bearer") != "Bearer"
        or not isinstance(client_id, str)
        or not client_id.strip()
        or (claims.get("azp") is not None and claims["azp"] != client_id)
        or (settings.oidc_allowed_clients and client_id not in settings.oidc_allowed_clients)
        or not isinstance(not_before, (float, int))
        or isinstance(not_before, bool)
        or not isfinite(not_before)
        or not_before > time()
        or not isinstance(scope, str)
        or (client_id in settings.oidc_mfa_clients and claims.get("acr") != "2")
    ):
        raise CoreError(ErrorCode.UNAUTHENTICATED)
    return VerifiedIdentity(
        issuer=claims["iss"],
        subject=claims["sub"],
        client_id=client_id,
        account_type="service" if client_id in settings.oidc_service_clients else "human",
        scopes=frozenset(scope.split()),
    )
