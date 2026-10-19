# SPDX-License-Identifier: GPL-3.0-or-later
"""Local test login through real Keycloak PKCE; keep short-lived credentials out of nodes."""

import argparse
import base64
import hashlib
import json
import secrets
import ssl
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from dotenv import set_key

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data"


class LoginForm(HTMLParser):
    action = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == "kc-form-login":
            self.action = attributes.get("action")


def configure(identity_dir: Path, watch: bool) -> None:
    runtime = json.loads((identity_dir / "runtime.json").read_text("utf-8"))
    operator = json.loads((identity_dir / "operator.json").read_text("utf-8"))
    profile = json.loads((STATE / "local-test.json").read_text("utf-8"))
    issuer = runtime["oidc_issuer"]
    if not issuer.startswith("https://localhost:") or "/realms/flowbridge" not in issuer:
        raise ValueError("ISOLATED_LOCAL_IDENTITY_REQUIRED")
    account = operator["test_users"]["employee"]
    redirect_uri = "http://127.0.0.1:8761/"
    client_id = "flowbridge-web-a"
    with httpx.Client(
        verify=ssl.create_default_context(cafile=runtime["oidc_ca_bundle"]),
        trust_env=False,
        follow_redirects=False,
        timeout=30,
    ) as client:
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(
            b"="
        )
        state = secrets.token_urlsafe(24)
        page = client.get(
            issuer + "/protocol/openid-connect/auth",
            params={
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid",
                "response_mode": "query",
                "state": state,
                "code_challenge": challenge.decode(),
                "code_challenge_method": "S256",
            },
        )
        page.raise_for_status()
        form = LoginForm()
        form.feed(page.text)
        if not form.action or not form.action.startswith(issuer + "/login-actions/"):
            raise ValueError("UNEXPECTED_LOGIN_FORM")
        login = client.post(
            form.action,
            data={
                "username": account["username"],
                "password": account["password"],
                "credentialId": "",
            },
        )
        location = login.headers.get("location", "")
        if not location.startswith(redirect_uri):
            raise ValueError("LOCAL_LOGIN_NOT_COMPLETED")
        params = parse_qs(urlparse(location).query)
        if params.get("state") != [state] or not params.get("code"):
            raise ValueError("INVALID_LOGIN_CALLBACK")
        response = client.post(
            issuer + "/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": params["code"][0],
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        response.raise_for_status()
        human = response.json()
        while True:
            service = client.post(
                issuer + "/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                },
                auth=("flowbridge-ai", operator["service_secrets"]["flowbridge-ai"]),
            )
            service.raise_for_status()
            values = {
                "FLOWBRIDGE_API_ORIGIN": "http://127.0.0.1:18083",
                "FLOWBRIDGE_USER_ACCESS_TOKEN": human["access_token"],
                "FLOWBRIDGE_AI_ACCESS_TOKEN": service.json()["access_token"],
                "FLOWBRIDGE_JOB_CONTEXT": profile["job_context"],
            }
            # Verify the genuine user token through the protected backend before publishing it.
            check = client.get(
                values["FLOWBRIDGE_API_ORIGIN"] + "/api/v1/products",
                params={
                    "page": 1,
                    "page_size": 1,
                },
                headers={"Authorization": "Bearer " + human["access_token"]},
            )
            check.raise_for_status()
            for name, value in values.items():
                set_key(str(STATE / "agent.env"), name, value, quote_mode="always")
            print("Real Keycloak ready; backend18083 authenticated; no node edits.", flush=True)
            if not watch:
                return
            time.sleep(max(10, min(int(human.get("expires_in", 180)) - 60, 90)))
            response = client.post(
                issuer + "/protocol/openid-connect/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": human["refresh_token"],
                },
            )
            response.raise_for_status()
            human = response.json()


def maintain_session(identity_dir: Path, watch: bool) -> None:
    """Re-enter genuine PKCE login after refresh expiry or transient HTTP failures."""
    retry_delay = 2
    while True:
        try:
            configure(identity_dir, watch)
            return
        except httpx.HTTPError as exc:
            if not watch:
                raise
            # A refresh token can expire while Langflow stays open. Reuse only the
            # existing isolated test profile; never publish unverified credentials.
            print(
                f"Local identity unavailable ({type(exc).__name__}); "
                f"retrying PKCE login in {retry_delay}s; credentials hidden.",
                flush=True,
            )
            time.sleep(retry_delay)
            retry_delay = min(30, retry_delay * 2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--identity-dir",
        type=Path,
        default=ROOT.parent / ".cache/rag/langflow-integration/identity",
    )
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    try:
        maintain_session(args.identity_dir, args.watch)
    except Exception as exc:
        print(f"Local identity stopped ({type(exc).__name__}); credentials hidden.", flush=True)
        raise SystemExit(1) from None
