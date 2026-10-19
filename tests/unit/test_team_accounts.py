# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import team_accounts as team  # noqa: E402


@pytest.mark.parametrize(
    "environment,issuer,admin",
    [
        ("production", "https://localhost:19444/realms/flowbridge", None),
        ("development", "https://example.org/realms/flowbridge", None),
        ("development", "http://localhost:19444/realms/flowbridge", None),
        ("development", "https://localhost:19444/realms/another", None),
        ("development", "https://localhost:19444/realms/flowbridge", "https://other/admin"),
    ],
)
def test_public_test_passwords_cannot_be_provisioned_to_another_profile(environment, issuer, admin):
    settings = SimpleNamespace(
        environment=environment,
        oidc_issuer=issuer,
        oidc_admin_url=admin or "https://localhost:19444/admin/realms/flowbridge",
    )
    with pytest.raises(team.TeamSetupError):
        team.require_local(settings)


@pytest.mark.asyncio
async def test_import_preserves_ids_and_retry_never_resets_password_or_otp():
    users = {}
    imports = []

    def handler(request):
        path = request.url.path
        if path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "test-token"})
        assert request.headers["Authorization"] == "Bearer test-token"
        if path.endswith("/partialImport"):
            payload = json.loads(request.content)
            assert payload["ifResourceExists"] == "SKIP"
            imports.append(payload)
            for user in payload["users"]:
                users[user["username"]] = user
            return httpx.Response(200, json={"added": len(payload["users"])})
        assert request.method == "GET", "Setup must not reset existing passwords or OTP"
        if path.endswith("/users"):
            user = users.get(request.url.params["username"])
            return httpx.Response(200, json=[user] if user else [])
        found = next(
            (user for user in users.values() if user["id"] == path.rsplit("/", 1)[1]), None
        )
        return httpx.Response(200, json=found) if found else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        operator = {"bootstrap_admin": "operator", "bootstrap_password": "operator-secret"}
        await team.provision_keycloak(http, "https://localhost:19444", operator)
        # A second independent machine derives the same IDs without a copied secret/state file.
        assert set(users) == set(team.USERNAMES)
        assert len({u["id"] for u in users.values()}) == len(team.USERNAMES)
        assert all(u["id"] == team.subject(name) for name, u in users.items())
        for user in users.values():
            assert user["credentials"][0]["value"] == team.PASSWORD
            user["credentials"] = [{"type": "otp"}, {"type": "password", "value": "changed"}]
        before = json.dumps(users)
        await team.provision_keycloak(http, "https://localhost:19444", operator)
        assert len(imports) == 1
        assert json.dumps(users) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["username", "disabled", "reserved-id"])
async def test_conflict_in_last_user_stops_before_any_iam_write(conflict):
    writes = []

    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "test-token"})
        if request.method != "GET":
            writes.append(request)
            return httpx.Response(200, json={})
        if request.url.path.endswith("/users"):
            if request.url.params["username"] == "tester03" and conflict != "reserved-id":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "username": "tester03",
                            "id": str(uuid4())
                            if conflict == "username"
                            else team.subject("tester03"),
                            "enabled": conflict != "disabled",
                        }
                    ],
                )
            return httpx.Response(200, json=[])
        if conflict == "reserved-id" and request.url.path.endswith(team.subject("tester03")):
            return httpx.Response(200, json={"username": "someone-else"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(team.TeamSetupError):
            await team.provision_keycloak(
                http,
                "https://localhost:19444",
                {
                    "bootstrap_admin": "operator",
                    "bootstrap_password": "operator-secret",
                },
            )
    assert not writes


@pytest.mark.asyncio
async def test_existing_unrelated_admin_requires_explicit_actor_and_validates_it():
    identifier = uuid4()
    repo = SimpleNamespace(
        active_admins=AsyncMock(return_value=[identifier]),
        find_identity=AsyncMock(return_value=None),
    )
    with pytest.raises(team.TeamSetupError, match="--granted-by"):
        await team.select_admin(repo, "issuer", {}, None)
    assert await team.select_admin(repo, "issuer", {}, identifier) == identifier
    with pytest.raises(team.TeamSetupError, match="active application ADMIN"):
        await team.select_admin(repo, "issuer", {}, uuid4())
    repo.active_admins.return_value = []
    assert await team.select_admin(repo, "issuer", {}, None) is None


@pytest.mark.asyncio
async def test_repeat_setup_uses_known_team_admin_instead_of_creating_a_second_bootstrap():
    identifier = uuid4()
    repo = SimpleNamespace(
        active_admins=AsyncMock(return_value=[identifier]),
        find_identity=AsyncMock(return_value={"user_id": identifier}),
    )
    assert await team.select_admin(repo, "issuer", {}, None) == identifier
    repo.find_identity.assert_awaited_once_with("issuer", team.subject("team-admin"))


def test_documented_password_and_account_table_match_setup():
    guide = (Path(__file__).resolve().parents[2] / "HUONG-DAN-SU-DUNG-BACKEND.md").read_text(
        "utf-8"
    )
    assert team.PASSWORD in guide
    assert all(username in guide for username in team.USERNAMES)
