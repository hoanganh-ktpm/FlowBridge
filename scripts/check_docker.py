"""Read-only engine authentication smoke; not publication/read/revoke E2E."""

import json
import ssl
from pathlib import Path

import httpx


def check():
    root = Path(__file__).resolve().parents[1]
    directory = root / ".tmp/docker-integration"
    state = json.loads((directory / "secrets.json").read_text())
    context = ssl.create_default_context(cafile=str(directory / "tls/server.crt"))
    issuer = "https://localhost:18443/realms/flowbridge-it"
    with httpx.Client(verify=context, timeout=20, follow_redirects=False) as client:
        response = client.get(issuer + "/.well-known/openid-configuration")
        response.raise_for_status()
        assert response.json()["issuer"] == issuer
        print("Keycloak HTTPS discovery: PASS (200, verified issuer)")
        response = client.post(
            issuer + "/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "flowbridge-e2e",
                "username": "test-it",
                "password": state["user_password"],
                "scope": "openid",
            },
        )
        response.raise_for_status()
        response = client.post(
            issuer + "/protocol/openid-connect/token/introspect",
            data={"token": response.json()["access_token"]},
            auth=("flowbridge-api", state["api_secret"]),
        )
        response.raise_for_status()
        claims = response.json()
        assert claims["active"] and claims["sub"] == state["users"]["it"]
        assert claims["iss"] == issuer and "flowbridge-api" in claims["aud"]
        print("Keycloak token/introspection: PASS (200, active/subject/audience verified)")
        response = client.get("http://127.0.0.1:18080/healthz")
        response.raise_for_status()
        print("Hasura health: PASS (200)")
        for headers, expected in (
            ({}, "invalid-headers"),
            ({"X-Hasura-Role": "admin"}, "invalid-headers"),
            (
                {"Authorization": "Bearer invalid-test-token", "X-Hasura-Role": "admin"},
                "invalid-jwt",
            ),
        ):
            response = client.post(
                "http://127.0.0.1:18080/v1/graphql",
                headers=headers,
                json={"query": "{__typename}"},
            )
            body = response.json()
            assert "data" not in body
            assert expected in {error.get("extensions", {}).get("code") for error in body["errors"]}
        print("Hasura missing/spoofed credentials: PASS (GraphQL errors, no data)")
    print("No Supabase writes; this smoke is not Data Sharing E2E")


if __name__ == "__main__":
    check()
