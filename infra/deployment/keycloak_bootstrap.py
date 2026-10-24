"""Add FlowBridge's managed User Profile field to an existing Keycloak realm."""

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml


class BootstrapError(Exception):
    """A safe, non-secret diagnostic for bootstrap failures."""


def managed_attribute(name):
    return {
        "name": name,
        "displayName": "Hasura default role",
        "multivalued": False,
        "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
    }


def merge_profile(profile, name):
    """Return a copy with only the owned field added, or reject unsafe conflicts."""
    if not isinstance(profile, dict) or not isinstance(profile.get("attributes"), list):
        raise BootstrapError("invalid Keycloak User Profile response")
    for attribute in profile["attributes"]:
        if attribute.get("name") != name:
            continue
        permissions = attribute.get("permissions", {})
        if (
            attribute.get("multivalued", False) is not False
            or set(permissions.get("edit", [])) != {"admin"}
            or not {"admin", "user"}.issubset(permissions.get("view", []))
        ):
            raise BootstrapError("managed User Profile field conflicts with FlowBridge policy")
        return profile, False
    updated = {**profile, "attributes": [*profile["attributes"], managed_attribute(name)]}
    return updated, True


def _request(url, *, method="GET", headers=None, body=None, form=False):
    data = None
    request_headers = dict(headers or {})
    if body is not None:
        data = urlencode(body).encode() if form else json.dumps(body).encode()
        request_headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            payload = response.read()
            return json.loads(payload) if payload else None
    except HTTPError as error:
        raise BootstrapError(f"Keycloak API returned HTTP {error.code}") from None
    except (OSError, URLError):
        raise BootstrapError("Keycloak API unavailable") from None


def bootstrap(config, stack, *, base_url=None):
    identity = config["identity"]
    attribute_name = identity.get("hasura_default_role_attribute", "hasura_default_role")
    realm = identity["realm"]
    base = (base_url or config["services"]["keycloak"]["public_url"]).rstrip("/")
    values = dict(
        line.split("=", 1)
        for line in (Path(stack) / "keycloak.env").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    token = _request(
        base + "/realms/master/protocol/openid-connect/token",
        method="POST",
        form=True,
        body={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": values["KC_BOOTSTRAP_ADMIN_USERNAME"],
            "password": values["KC_BOOTSTRAP_ADMIN_PASSWORD"],
        },
    )["access_token"]
    headers = {"Authorization": "Bearer " + token}
    profile_url = base + f"/admin/realms/{realm}/users/profile"
    current = _request(profile_url, headers=headers)
    updated, changed = merge_profile(current, attribute_name)
    if not changed:
        return False
    if _request(profile_url, headers=headers) != current:
        raise BootstrapError("Keycloak User Profile changed during bootstrap")
    _request(profile_url, method="PUT", headers=headers, body=updated)
    _, still_missing = merge_profile(_request(profile_url, headers=headers), attribute_name)
    if still_missing:
        raise BootstrapError("Keycloak User Profile verification failed")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("deployment.yaml"))
    parser.add_argument("--stack", type=Path, default=Path("infra/deployment/generated"))
    args = parser.parse_args()
    try:
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
        changed = bootstrap(config, args.stack)
    except (BootstrapError, OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        message = (
            str(error) if isinstance(error, BootstrapError) else "invalid bootstrap configuration"
        )
        print("FAIL: " + message, file=sys.stderr)
        return 1
    print("PASS: Keycloak User Profile " + ("configured" if changed else "unchanged"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
