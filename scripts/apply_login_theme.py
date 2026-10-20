"""Apply only a Keycloak login theme; preserve accounts, grants and realm policy.

Credentials are read from the existing private launcher export, never CLI arguments.
"""
from __future__ import annotations

import argparse
import json
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import Request, urlopen


def apply_theme(origin: str, realm: str, theme: str, credentials: Path, ca: Path) -> None:
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("Use an HTTPS identity origin without a path or credentials.")
    origin = origin.rstrip("/")
    tls = ssl.create_default_context(cafile=str(ca))
    account = json.loads(credentials.read_text("utf-8"))["keycloak_admin"]
    token_request = Request(
        origin + "/realms/master/protocol/openid-connect/token",
        data=urlencode({"client_id": "admin-cli", "grant_type": "password", "username": account["username"], "password": account["password"]}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(token_request, context=tls, timeout=15) as response:
        token = json.load(response)["access_token"]
    del account, token_request

    def request(path: str, body: dict | None = None):
        headers = {"Authorization": "Bearer " + token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(origin + path, headers=headers, data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "PUT")
        with urlopen(req, context=tls, timeout=15) as response:
            content = response.read()
            return json.loads(content) if content else None

    installed = request("/admin/serverinfo")["themes"]["login"]
    if not any(item["name"] == theme for item in installed):
        raise ValueError("The requested login theme is not installed in this Keycloak instance.")
    path = "/admin/realms/" + quote(realm, safe="")
    before = request(path)
    request(path, {"loginTheme": theme})
    after = request(path)
    if after.get("loginTheme") != theme or after.get("registrationAllowed") != before.get("registrationAllowed"):
        raise ValueError("Theme verification failed or registration policy changed concurrently.")
    print(json.dumps({"realm": realm, "login_theme": after["loginTheme"], "registration_allowed": after.get("registrationAllowed", False)}, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://localhost:19444")
    parser.add_argument("--realm", default="flowbridge")
    parser.add_argument("--theme", default="flowbridge")
    parser.add_argument("--credentials", type=Path, default=Path(".cache/chat-stack/flowbridge-chat/credentials.json"))
    parser.add_argument("--ca", type=Path, required=True)
    args = parser.parse_args()
    try:
        apply_theme(args.origin, args.realm, args.theme, args.credentials, args.ca)
    except HTTPError as error:
        raise SystemExit(f"Identity theme request failed (HTTP {error.code}); verify the local operator configuration.") from None
