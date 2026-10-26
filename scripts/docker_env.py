"""Prepare local engine secrets/TLS; never create, migrate or reset Supabase databases."""

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

from scripts.integration_utils import write_env


def prepare():
    root = Path(__file__).resolve().parents[1]
    directory = root / ".tmp/docker-integration"
    state_file = directory / "secrets.json"
    env_file = root / ".env.docker"
    if state_file.exists():
        if not env_file.exists():
            raise ValueError(
                "Existing runtime state is incomplete; restore .env.docker, "
                "do not rotate implicitly"
            )
        print("Existing Docker test configuration retained; no secret/realm/volume reset")
        return
    if env_file.exists():
        raise ValueError("Refusing to overwrite an existing .env.docker")
    git_openssl = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/openssl.exe"
    )
    openssl = git_openssl if git_openssl.is_file() else shutil.which("openssl")
    if not openssl:
        raise ValueError("OpenSSL is required to prepare the local HTTPS certificate")
    values = {**dotenv_values(root / ".env"), **os.environ}
    core_url = values.get("DXOS_CORE_URL")
    if not core_url:
        raise ValueError("Set DXOS_CORE_URL to the existing Core database; no DB is created")
    (directory / "tls").mkdir(parents=True, exist_ok=True)
    (directory / "realm").mkdir(exist_ok=True)
    state = {
        name: secrets.token_urlsafe(36)
        for name in (
            "admin_password",
            "api_secret",
            "directory_secret",
            "hasura_secret",
            "signing_key",
            "user_password",
        )
    }
    state["users"] = {
        name: str(uuid4()) for name in ("it", "sales", "outsider", "disabled", "unregistered")
    }
    certificate = directory / "tls/server.crt"
    key = directory / "tls/server.key"
    subprocess.run(
        [
            str(openssl),
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "7",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    template = (root / "infra/keycloak/realm.template.json").read_text(encoding="utf-8")
    for name, value in {
        "SSO_ORIGIN_A": "http://127.0.0.1:5174",
        "SSO_API_SECRET": state["api_secret"],
        "SSO_DIRECTORY_SECRET": state["directory_secret"],
    }.items():
        template = template.replace("${" + name + "}", value)
    realm = json.loads(template)
    realm["realm"] = "flowbridge-it"
    realm["accessTokenLifespan"] = 600
    realm["clients"].append(
        {
            "clientId": "flowbridge-e2e",
            "protocol": "openid-connect",
            "enabled": True,
            "publicClient": True,
            "standardFlowEnabled": False,
            "directAccessGrantsEnabled": True,
            "defaultClientScopes": ["flowbridge-api-audience"],
            "attributes": {"access.token.lifespan": "600"},
        }
    )
    realm["users"].extend(
        {
            "id": identifier,
            "username": "test-" + name,
            "enabled": True,
            "emailVerified": True,
            "firstName": "Integration",
            "lastName": name,
            "email": name + "@example.invalid",
            "requiredActions": [],
            "credentials": [
                {"type": "password", "value": state["user_password"], "temporary": False}
            ],
        }
        for name, identifier in state["users"].items()
    )
    (directory / "realm/flowbridge-it-realm.json").write_text(
        json.dumps(realm, indent=2), encoding="utf-8"
    )
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    write_env(
        env_file,
        {
            "FLOWBRIDGE_DOCKER_RUNTIME_DIR": directory.as_posix(),
            "FLOWBRIDGE_KEYCLOAK_ADMIN_PASSWORD": state["admin_password"],
            "FLOWBRIDGE_HASURA_ADMIN_SECRET": state["hasura_secret"],
            "FLOWBRIDGE_HASURA_JWT_CONFIG": json.dumps(
                {
                    "type": "HS256",
                    "key": state["signing_key"],
                    "issuer": "flowbridge-core",
                    "audience": "flowbridge-data",
                }
            ),
        },
    )
    issuer = "https://localhost:18443/realms/flowbridge-it"
    write_env(
        directory / "core.env",
        {
            "FLOWBRIDGE_CORE_DATABASE_URL": core_url,
            "FLOWBRIDGE_OIDC_ISSUER": issuer,
            "FLOWBRIDGE_OIDC_AUDIENCE": "flowbridge-api",
            "FLOWBRIDGE_OIDC_INTROSPECTION_URL": issuer
            + "/protocol/openid-connect/token/introspect",
            "FLOWBRIDGE_OIDC_CLIENT_ID": "flowbridge-api",
            "FLOWBRIDGE_OIDC_CLIENT_SECRET": state["api_secret"],
            "FLOWBRIDGE_OIDC_ALLOWED_CLIENTS": '["flowbridge-e2e"]',
            "FLOWBRIDGE_OIDC_CA_BUNDLE": certificate.as_posix(),
            "FLOWBRIDGE_HASURA_ENDPOINT": "http://127.0.0.1:18080",
            "FLOWBRIDGE_HASURA_ADMIN_SECRET": state["hasura_secret"],
            "FLOWBRIDGE_CORE_HASURA_JWT_SIGNING_KEY": state["signing_key"],
            "FLOWBRIDGE_CORE_VERIFIED_HASURA_AUTH_PROFILE": "false",
        },
    )
    print("Prepared .env.docker and ignored .tmp/docker-integration runtime files")
    print("Supabase configuration retained; no database mutation, migration or reset")


if __name__ == "__main__":
    prepare()
