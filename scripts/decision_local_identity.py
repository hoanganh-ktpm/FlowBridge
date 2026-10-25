# SPDX-License-Identifier: GPL-3.0-or-later
"""Short-lived machine identities for local API tests, never impersonating MFA users."""

import json
import secrets
import ssl
from contextlib import contextmanager
from uuid import uuid4

import httpx

from scripts.integration_utils import RUNTIME

BASE = "https://localhost:18443"
REALM = "flowbridge-it"


def client_definition(name, secret):
    return {
        "clientId": name,
        "secret": secret,
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "serviceAccountsEnabled": True,
        "standardFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "protocolMappers": [
            {
                "name": "flowbridge-test-api-audience",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "config": {
                    "included.client.audience": "flowbridge-api",
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                },
            }
        ],
    }


def checked(response, expected):
    if response.status_code != expected:
        raise RuntimeError(f"Local machine identity operation returned HTTP {response.status_code}")
    return response


@contextmanager
def machine_test_identities():
    """Only creates/removes owned clients; browser/MFA policies remain untouched."""
    state = json.loads((RUNTIME / "secrets.json").read_text())
    ca = ssl.create_default_context(cafile=str(RUNTIME / "tls/server.crt"))
    run_id = uuid4().hex
    prefix = "decision-test-" + run_id + "-"
    created, users, clients, auth = [], {}, {}, {}
    directory = RUNTIME / "machine-identities" / run_id
    directory.mkdir(parents=True)

    def record(status):
        (directory / "resources.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "issuer": f"{BASE}/realms/{REALM}",
                    "prefix": prefix,
                    "clients": [{"id": key, "name": value} for key, value in created],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    with httpx.Client(base_url=BASE, verify=ca, timeout=20, follow_redirects=False) as http:

        def admin_headers():
            response = checked(
                http.post(
                    "/realms/master/protocol/openid-connect/token",
                    data={
                        "grant_type": "password",
                        "client_id": "admin-cli",
                        "username": "flowbridge-test-admin",
                        "password": state["admin_password"],
                    },
                ),
                200,
            )
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        headers = admin_headers()
        endpoint = f"/admin/realms/{REALM}/clients"
        try:
            for role in ("it", "sales", "outsider"):
                name, secret = prefix + role, secrets.token_urlsafe(36)
                checked(
                    http.post(endpoint, headers=headers, json=client_definition(name, secret)), 201
                )
                found = checked(
                    http.get(endpoint, headers=headers, params={"clientId": name}), 200
                ).json()
                if len(found) != 1 or found[0]["clientId"] != name:
                    raise RuntimeError("Cannot identify the owned machine test client")
                identifier = found[0]["id"]
                created.append((identifier, name))
                record("ACTIVE")
                user = checked(
                    http.get(endpoint + f"/{identifier}/service-account-user", headers=headers), 200
                ).json()
                token = checked(
                    http.post(
                        f"/realms/{REALM}/protocol/openid-connect/token",
                        data={"grant_type": "client_credentials"},
                        auth=(name, secret),
                    ),
                    200,
                ).json()["access_token"]
                claims = checked(
                    http.post(
                        f"/realms/{REALM}/protocol/openid-connect/token/introspect",
                        data={"token": token},
                        auth=("flowbridge-api", state["api_secret"]),
                    ),
                    200,
                ).json()
                if not (
                    claims.get("active")
                    and claims.get("sub") == user["id"]
                    and claims.get("client_id") == name
                    and claims.get("iss") == f"{BASE}/realms/{REALM}"
                    and "flowbridge-api" in claims.get("aud", [])
                ):
                    raise RuntimeError("Machine test identity claims do not match its binding")
                users[role], clients[role], auth[role] = user["id"], name, token
            yield (
                auth,
                {"users": users, "service_clients": clients},
                {
                    "FLOWBRIDGE_OIDC_ALLOWED_CLIENTS": json.dumps(list(clients.values())),
                    "FLOWBRIDGE_OIDC_SERVICE_CLIENTS": json.dumps(list(clients.values())),
                },
            )
        finally:
            # Master-realm admin access tokens can expire while the test is running.
            headers = admin_headers()
            for identifier, name in reversed(created):
                owned = checked(http.get(endpoint + "/" + identifier, headers=headers), 200).json()
                if owned["clientId"] != name or not name.startswith(prefix):
                    raise RuntimeError("Refusing to remove an identity outside this test run")
                checked(http.delete(endpoint + "/" + identifier, headers=headers), 204)
            record("CLEANED")
