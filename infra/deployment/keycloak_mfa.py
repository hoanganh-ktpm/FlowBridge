"""Inspect/apply client-scoped MFA on the existing Keycloak 26.8.1 realm.

Never imports a realm, changes users/credentials, logs out sessions or edits other clients.
The canonical flow and mapper are in infra/keycloak/realm.template.json.
"""

import argparse
import json
import os
import ssl
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx


class MfaError(Exception):
    """Safe diagnostic; upstream bodies can contain credentials and are never printed."""


def _request(client, method, url, **kwargs):
    try:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None
    except httpx.HTTPStatusError as error:
        raise MfaError(f"Keycloak API returned HTTP {error.response.status_code}") from None
    except (httpx.HTTPError, ValueError):
        raise MfaError("Keycloak API unavailable or invalid response") from None


def configure_mfa(client, base, realm, client_id, *, apply=False):
    """Return safe evidence; bind only after verifying every authenticator and LoA config."""
    template = json.loads(
        (Path(__file__).parents[1] / "keycloak/realm.template.json").read_text(encoding="utf-8")
    )
    root = template["authenticationFlows"][0]
    definitions = {flow["alias"]: flow for flow in template["authenticationFlows"]}
    config = template["authenticatorConfig"][0]
    mapper = template["clients"][0]["protocolMappers"][0]
    version = _request(client, "GET", base + "/admin/serverinfo")["systemInfo"]["version"]
    if version != "26.8.1":
        raise MfaError("MFA provisioning is verified only for Keycloak 26.8.1")
    api = base + "/admin/realms/" + quote(realm, safe="")
    current_realm = _request(client, "GET", api)
    for key in ("otpPolicyType", "otpPolicyAlgorithm", "otpPolicyDigits", "otpPolicyPeriod"):
        if current_realm.get(key) != template[key]:
            raise MfaError("Existing realm OTP policy differs; review it before applying MFA")
    clients = _request(client, "GET", api + "/clients", params={"clientId": client_id})
    if len(clients) != 1 or clients[0]["clientId"] != client_id:
        raise MfaError("Expected exactly one existing browser client")
    web = _request(client, "GET", api + "/clients/" + clients[0]["id"])
    if not web.get("publicClient") or not web.get("standardFlowEnabled"):
        raise MfaError("Target must be an existing public Authorization Code browser client")
    action = _request(client, "GET", api + "/authentication/required-actions/CONFIGURE_TOTP")
    if not action.get("enabled"):
        raise MfaError("Enable native Configure OTP required action before applying MFA")
    flows = _request(client, "GET", api + "/authentication/flows")
    owned = {flow["alias"]: flow for flow in flows if flow["alias"] == root["alias"]}
    changed = not owned
    if not owned and apply:
        _request(
            client,
            "POST",
            api + "/authentication/flows",
            json={
                key: root[key]
                for key in ("alias", "description", "providerId", "topLevel", "builtIn")
            },
        )

        def populate(flow):
            for execution in flow["authenticationExecutions"]:
                path = api + f"/authentication/flows/{flow['alias']}/executions"
                nested = execution.get("flowAlias")
                if nested:
                    _request(
                        client,
                        "POST",
                        path + "/flow",
                        json={
                            "alias": nested,
                            "type": "basic-flow",
                            "provider": "basic-flow",
                        },
                    )
                else:
                    _request(
                        client,
                        "POST",
                        path + "/execution",
                        json={"provider": execution["authenticator"]},
                    )
                created = _request(client, "GET", path)[-1]
                _request(
                    client, "PUT", path, json={**created, "requirement": execution["requirement"]}
                )
                if nested:
                    populate(definitions[nested])
                elif execution.get("authenticatorConfig"):
                    _request(
                        client,
                        "POST",
                        api + f"/authentication/executions/{created['id']}/config",
                        json=config,
                    )

        populate(root)
        flows = _request(client, "GET", api + "/authentication/flows")
        owned = {f["alias"]: f for f in flows if f["alias"] == root["alias"]}
    if owned:
        if owned[root["alias"]].get("providerId") != "basic-flow" or not owned[root["alias"]].get(
            "topLevel"
        ):
            raise MfaError("Managed MFA root flow conflicts with policy")

        def verify(flow):
            actual = _request(
                client, "GET", api + f"/authentication/flows/{flow['alias']}/executions"
            )
            direct = [e for e in actual if e["level"] == 0]
            expected = flow["authenticationExecutions"]
            if len(direct) != len(expected):
                raise MfaError("Managed MFA flow conflicts with policy; refusing to bind")
            for found, wanted in zip(direct, expected, strict=True):
                if found["requirement"] != wanted["requirement"] or found.get(
                    "providerId"
                ) != wanted.get("authenticator"):
                    raise MfaError("Managed MFA execution conflicts with policy")
                if wanted.get("flowAlias"):
                    subflow = _request(
                        client, "GET", api + "/authentication/flows/" + found["flowId"]
                    )
                    if (
                        subflow.get("alias") != wanted["flowAlias"]
                        or subflow.get("providerId") != "basic-flow"
                    ):
                        raise MfaError("Unexpected MFA subflow")
                    verify(definitions[wanted["flowAlias"]])
                elif wanted.get("authenticatorConfig"):
                    loa = _request(
                        client,
                        "GET",
                        api + "/authentication/config/" + found["authenticationConfig"],
                    )
                    if loa["config"] != config["config"]:
                        raise MfaError("Managed MFA assurance configuration conflicts with policy")

        verify(root)
    binding = web.get("authenticationFlowBindingOverrides", {})
    desired = {
        "directAccessGrantsEnabled": False,
        "implicitFlowEnabled": False,
        "serviceAccountsEnabled": False,
        "attributes": {
            **web.get("attributes", {}),
            **{
                key: template["clients"][0]["attributes"][key]
                for key in (
                    "pkce.code.challenge.method",
                    "minimum.acr.value",
                    "default.acr.values",
                    "acr.loa.map",
                )
            },
        },
    }
    if owned:
        desired["authenticationFlowBindingOverrides"] = {
            **binding,
            "browser": owned[root["alias"]]["id"],
        }
    needs_client = any(web.get(key) != value for key, value in desired.items()) or not owned
    mappers = _request(client, "GET", api + f"/clients/{web['id']}/protocol-mappers/models")
    acr = [m for m in mappers if m["name"] == mapper["name"]]
    if acr and (
        any(acr[0].get(key) != value for key, value in mapper.items() if key != "config")
        or any(
            acr[0].get("config", {}).get(key) != value for key, value in mapper["config"].items()
        )
    ):
        raise MfaError("Managed ACR mapper conflicts with policy")
    if apply:
        if _request(client, "GET", api + "/clients/" + web["id"]) != web:
            raise MfaError("Client changed during MFA provisioning")
        if not acr:
            _request(
                client, "POST", api + f"/clients/{web['id']}/protocol-mappers/models", json=mapper
            )
        if needs_client:
            _request(client, "PUT", api + "/clients/" + web["id"], json=desired)
        verified = configure_mfa(client, base, realm, client_id)
        if not verified["enforced"]:
            raise MfaError("MFA configuration verification failed")
        return {**verified, "changed": changed or needs_client or not acr}
    return {
        "realm": realm,
        "client": client_id,
        "keycloak_version": version,
        "flow": root["alias"],
        "enforced": bool(owned) and not needs_client and bool(acr),
        "changed": changed or needs_client or not acr,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--realm", required=True)
    parser.add_argument("--client-id", default="flowbridge-web-a")
    parser.add_argument("--admin-user", required=True)
    parser.add_argument("--password-env", default="FLOWBRIDGE_KEYCLOAK_ADMIN_PASSWORD")
    parser.add_argument("--ca-bundle", type=Path)
    parser.add_argument("--apply", action="store_true", help="Default is read-only inspection")
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if (
        (
            url.scheme != "https"
            and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"})
        )
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        parser.error("Use an HTTPS Keycloak base URL (HTTP permitted only on loopback)")
    try:
        tls = ssl.create_default_context(cafile=str(args.ca_bundle) if args.ca_bundle else None)
        with httpx.Client(verify=tls, timeout=15) as client:
            token = _request(
                client,
                "POST",
                args.base_url.rstrip("/") + "/realms/master/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": args.admin_user,
                    "password": os.environ[args.password_env],
                },
            )
            client.headers["Authorization"] = "Bearer " + token["access_token"]
            result = configure_mfa(
                client, args.base_url.rstrip("/"), args.realm, args.client_id, apply=args.apply
            )
            print(json.dumps(result))
    except (MfaError, OSError, KeyError, TypeError, ValueError) as error:
        print(
            "FAIL: " + (str(error) if isinstance(error, MfaError) else "Invalid MFA configuration")
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
