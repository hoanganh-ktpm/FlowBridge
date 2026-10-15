# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Generate local configuration and TLS with OpenSSL; authentication remains in Keycloak.
import argparse
import base64
import hashlib
import json
import secrets
import subprocess
from pathlib import Path
from string import Template
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def prepare(out: Path, port: int, api_url: str, test_users: bool) -> None:
    out = out.resolve()
    if (out / "operator.json").exists():
        raise RuntimeError(
            "Identity configuration already exists; reuse it instead of rotating secrets"
        )
    out.mkdir(parents=True, exist_ok=True)
    certs = out / "certs"
    certs.mkdir(exist_ok=True)

    def openssl(*args: str) -> bytes:
        return subprocess.run(["openssl", *args], check=True, capture_output=True).stdout

    openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "30",
        "-sha256",
        "-subj",
        "/CN=FlowBridge local identity CA",
        "-keyout",
        str(certs / "ca.key"),
        "-out",
        str(certs / "ca.pem"),
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    openssl(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=localhost",
        "-keyout",
        str(certs / "server.key"),
        "-out",
        str(certs / "server.csr"),
    )
    extensions = certs / "server.ext"
    extensions.write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        "basicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\n",
        encoding="ascii",
    )
    openssl(
        "x509",
        "-req",
        "-in",
        str(certs / "server.csr"),
        "-CA",
        str(certs / "ca.pem"),
        "-CAkey",
        str(certs / "ca.key"),
        "-CAcreateserial",
        "-days",
        "30",
        "-sha256",
        "-extfile",
        str(extensions),
        "-out",
        str(certs / "server.pem"),
    )
    public_key = openssl("x509", "-in", str(certs / "server.pem"), "-pubkey", "-noout")
    der = subprocess.run(
        ["openssl", "pkey", "-pubin", "-outform", "DER"],
        input=public_key,
        check=True,
        capture_output=True,
    ).stdout
    spki = base64.b64encode(hashlib.sha256(der).digest()).decode("ascii")
    issuer = f"https://localhost:{port}/realms/flowbridge"
    values = {
        "SSO_" + name + "_SECRET": secrets.token_urlsafe(32)
        for name in ("API", "DIRECTORY", "AI", "PO")
    }
    values.update(SSO_ORIGIN_A="http://127.0.0.1:8761", SSO_ORIGIN_B="http://127.0.0.1:8762")
    template = json.loads((ROOT / "infra/identity/realm.template.json").read_text("utf-8"))

    def render(value: object) -> object:
        if isinstance(value, str):
            return Template(value).substitute(values)
        if isinstance(value, list):
            return [render(item) for item in value]
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        return value

    realm = render(template)
    accounts = {}
    if test_users:
        for code in ("admin", "employee", "other"):
            account = {
                "id": str(uuid4()),
                "username": "flowbridge-" + code,
                "password": secrets.token_urlsafe(24),
            }
            accounts[code] = account
            realm["users"].append(
                {
                    "id": account["id"],
                    "username": account["username"],
                    "firstName": code,
                    "lastName": "SSO",
                    "email": code + "@example.invalid",
                    "enabled": True,
                    "emailVerified": True,
                    "requiredActions": [],
                    "credentials": [
                        {"type": "password", "value": account["password"], "temporary": False}
                    ],
                }
            )
    runtime = {
        "oidc_issuer": issuer,
        "oidc_audience": "flowbridge-api",
        "oidc_introspection_url": issuer + "/protocol/openid-connect/token/introspect",
        "oidc_client_id": "flowbridge-api",
        "oidc_client_secret": values["SSO_API_SECRET"],
        "oidc_ca_bundle": str(certs / "ca.pem"),
        "oidc_admin_url": f"https://localhost:{port}/admin/realms/flowbridge",
        "oidc_admin_client_id": "flowbridge-directory",
        "oidc_admin_client_secret": values["SSO_DIRECTORY_SECRET"],
        "oidc_allowed_clients": [
            "flowbridge-web-a",
            "flowbridge-web-b",
            "flowbridge-ai",
            "flowbridge-po",
        ],
        "oidc_service_clients": ["flowbridge-ai", "flowbridge-po"],
        "cors_origins": [values["SSO_ORIGIN_A"], values["SSO_ORIGIN_B"]],
        "docs_enabled": True,
    }
    public = {
        "issuer": issuer,
        "apiUrl": api_url,
        "clients": {"a": "flowbridge-web-a", "b": "flowbridge-web-b"},
    }
    operator = {
        "port": port,
        "bootstrap_admin": "flowbridge-local-admin",
        "bootstrap_password": secrets.token_urlsafe(32),
        "test_users": accounts,
        "service_secrets": {
            "flowbridge-ai": values["SSO_AI_SECRET"],
            "flowbridge-po": values["SSO_PO_SECRET"],
        },
        "spki": spki,
    }
    for name, value in (
        ("realm.json", realm),
        ("runtime.json", runtime),
        ("public.json", public),
        ("operator.json", operator),
    ):
        (out / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Local identity configuration: {out}. Secrets stay in ignored files; issuer: {issuer}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare local Keycloak configuration; does not start it"
    )
    parser.add_argument("--out", type=Path, default=ROOT / ".cache/identity")
    parser.add_argument("--port", type=int, default=18443)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--test-users", action="store_true", help="Create isolated real IdP test users"
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    try:
        prepare(args.out, args.port, args.api_url, args.test_users)
    except Exception as error:
        print(f"Identity preparation failed ({type(error).__name__}); check paths and OpenSSL")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
