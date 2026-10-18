# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Read verified identity metadata through Keycloak's supported Admin REST API.
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import httpx

from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode


@dataclass(frozen=True, slots=True)
class DirectoryIdentity:
    issuer: str
    subject: str
    account_type: Literal["human", "service"]
    display_name: str
    email: str | None
    client_id: str | None = None


class KeycloakDirectory:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self.http, self.settings = http, settings

    async def resolve(self, reference: str) -> DirectoryIdentity:
        try:
            subject = str(UUID(reference))
        except ValueError:
            raise CoreError(ErrorCode.VALIDATION_ERROR) from None
        settings = self.settings
        if not all(
            (
                settings.oidc_issuer,
                settings.oidc_admin_url,
                settings.oidc_admin_client_id,
                settings.oidc_admin_client_secret,
            )
        ):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        try:
            token_response = await self.http.post(
                settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/token",
                data={"grant_type": "client_credentials"},
                auth=httpx.BasicAuth(
                    settings.oidc_admin_client_id,
                    settings.oidc_admin_client_secret.get_secret_value(),
                ),
            )
            token_response.raise_for_status()
            token = token_response.json().get("access_token")
            if not isinstance(token, str) or not token:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            response = await self.http.get(
                settings.oidc_admin_url.rstrip("/") + "/users/" + subject,
                headers={"Authorization": "Bearer " + token},
            )
            if response.status_code == 404:
                raise CoreError(ErrorCode.NOT_FOUND)
            response.raise_for_status()
            user = response.json()
        except httpx.TimeoutException:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        except (httpx.HTTPError, ValueError, AttributeError):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        if not isinstance(user, dict) or user.get("id") != subject:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        if user.get("enabled") is not True:
            raise CoreError(ErrorCode.FORBIDDEN)
        client = user.get("serviceAccountClientId")
        if client is not None and client not in settings.oidc_service_clients:
            raise CoreError(ErrorCode.FORBIDDEN)
        # GET /users does not reliably include serviceAccountClientId. Verify the binding
        # through the provider's service-account-user endpoint instead of trusting a name.
        bound_client = None
        try:
            for registered in settings.oidc_service_clients:
                clients = await self.http.get(
                    settings.oidc_admin_url.rstrip("/") + "/clients",
                    params={"clientId": registered},
                    headers={"Authorization": "Bearer " + token},
                )
                clients.raise_for_status()
                matches = clients.json()
                if (
                    not isinstance(matches, list)
                    or len(matches) != 1
                    or matches[0].get("clientId") != registered
                ):
                    raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
                internal_id = str(UUID(matches[0]["id"]))
                account = await self.http.get(
                    settings.oidc_admin_url.rstrip("/")
                    + "/clients/"
                    + internal_id
                    + "/service-account-user",
                    headers={"Authorization": "Bearer " + token},
                )
                account.raise_for_status()
                if account.json().get("id") == subject:
                    bound_client = registered
        except httpx.TimeoutException:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        if client is not None and client != bound_client:
            raise CoreError(ErrorCode.FORBIDDEN)
        client = bound_client
        name = user.get("username")
        email = user.get("email")
        if not isinstance(name, str) or not name.strip():
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        if name.startswith("service-account-") and client is None:
            raise CoreError(ErrorCode.FORBIDDEN)
        return DirectoryIdentity(
            issuer=settings.oidc_issuer,
            subject=subject,
            account_type="service" if client else "human",
            display_name=name[:255],
            email=email if isinstance(email, str) and len(email) <= 255 else None,
            client_id=client,
        )
