"""Shared helpers for local integration harnesses."""

import json
import ssl
from pathlib import Path

import httpx
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".tmp/docker-integration"


def write_env(path, values):
    path.write_text(
        "\n".join(f"{name}={json.dumps(str(value))}" for name, value in values.items()) + "\n",
        encoding="utf-8",
    )


def engine_for(url):
    return create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10})


def tokens():
    state = json.loads((RUNTIME / "secrets.json").read_text())
    issuer = "https://localhost:18443/realms/flowbridge-it"
    context = ssl.create_default_context(cafile=str(RUNTIME / "tls/server.crt"))
    result = {}
    with httpx.Client(verify=context, timeout=20) as client:
        for name, subject in state["users"].items():
            response = client.post(
                issuer + "/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "flowbridge-e2e",
                    "username": "test-" + name,
                    "password": state["user_password"],
                    "scope": "openid",
                },
            )
            response.raise_for_status()
            token = response.json()["access_token"]
            response = client.post(
                issuer + "/protocol/openid-connect/token/introspect",
                data={"token": token},
                auth=("flowbridge-api", state["api_secret"]),
            )
            response.raise_for_status()
            claims = response.json()
            assert claims["active"] and claims["iss"] == issuer and claims["sub"] == subject
            assert "flowbridge-api" in claims["aud"]
            result[name] = token
    return result
