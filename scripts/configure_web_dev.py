# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.
# Register only the fixed loopback Vite callback with the existing public client.
import argparse
import json
import ssl
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

DEV_ORIGIN = "http://127.0.0.1:5174"
CLIENT_ID = "flowbridge-web-a"


def configure(origin: str, credentials: Path, ca: Path, *, apply: bool) -> dict[str, Any]:
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
    ):
        raise ValueError("Use the HTTPS loopback origin of the existing local identity.")
    origin = origin.rstrip("/")
    tls = ssl.create_default_context(cafile=str(ca))
    account = json.loads(credentials.read_text("utf-8"))["keycloak_admin"]
    request = Request(
        origin + "/realms/master/protocol/openid-connect/token",
        data=urlencode({
            "client_id": "admin-cli", "grant_type": "password",
            "username": account["username"], "password": account["password"],
        }).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, context=tls, timeout=15) as response:
        token = json.load(response)["access_token"]
    del account, request

    def admin(path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {"Authorization": "Bearer " + token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(
            origin + path, headers=headers,
            data=None if body is None else json.dumps(body).encode(),
            method="GET" if body is None else "PUT",
        )
        with urlopen(req, context=tls, timeout=15) as response:
            content = response.read()
            return json.loads(content) if content else None

    clients_path = "/admin/realms/flowbridge/clients"
    clients = admin(clients_path + "?" + urlencode({"clientId": CLIENT_ID}))
    if len(clients) != 1 or clients[0]["clientId"] != CLIENT_ID:
        raise ValueError("Expected exactly one existing FlowBridge web client.")
    path = clients_path + "/" + quote(clients[0]["id"], safe="")
    before = admin(path)
    if (
        before.get("publicClient") is not True
        or before.get("standardFlowEnabled") is not True
        or before.get("implicitFlowEnabled") is not False
        or before.get("directAccessGrantsEnabled") is not False
        or before.get("attributes", {}).get("pkce.code.challenge.method") != "S256"
    ):
        raise ValueError("The existing public client must retain Code + PKCE S256.")
    attributes = dict(before.get("attributes", {}))
    logout_key = "post.logout.redirect.uris"
    logout_uris = [uri for uri in attributes.get(logout_key, "").split("##") if uri]
    attributes[logout_key] = "##".join(dict.fromkeys([*logout_uris, DEV_ORIGIN + "/"]))
    patch = {
        "redirectUris": list(dict.fromkeys([*before.get("redirectUris", []), DEV_ORIGIN + "/"])),
        "webOrigins": list(dict.fromkeys([*before.get("webOrigins", []), DEV_ORIGIN])),
        "attributes": attributes,
    }
    if apply:
        # Stop if a concurrent administrator changed the client since the read.
        if admin(path) != before:
            raise ValueError("Client changed concurrently; inspect and rerun.")
        admin(path, patch)
        after = admin(path)
        if (
            set(after.get("redirectUris", [])) != set(patch["redirectUris"])
            or set(after.get("webOrigins", [])) != set(patch["webOrigins"])
            or after.get("attributes") != attributes
        ):
            raise ValueError("Callback verification failed.")
        for key in before.keys() | after.keys():
            if key not in patch and before.get(key) != after.get(key):
                raise ValueError("An unrelated client setting changed; inspect before continuing.")
    return {
        "applied": apply, "client_id": CLIENT_ID,
        "redirect_uris": patch["redirectUris"], "web_origins": patch["webOrigins"],
        "pkce": "S256", "post_logout_redirect_uris": attributes[logout_key].split("##"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register the fixed local Vite SSO callback.")
    parser.add_argument("--origin", default="https://localhost:19444")
    parser.add_argument(
        "--credentials", type=Path,
        default=Path(".cache/chat-stack/flowbridge-chat/credentials.json"),
    )
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(configure(args.origin, args.credentials, args.ca, apply=args.apply)))
    except HTTPError as error:
        raise SystemExit(f"Local identity request failed (HTTP {error.code}).") from None
