# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.

# Explicit local contest setup using Keycloak partial import and the existing account policy.
import argparse
import asyncio
import json
import os
import ssl
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from flowbridge.db import create_engine, create_session_factory
from flowbridge.identity.account_repository import IdentityAccountRepository
from flowbridge.identity.bootstrap import bootstrap_identity
from flowbridge.identity.directory import KeycloakDirectory
from flowbridge.models import Department
from flowbridge.settings import Settings

PASSWORD = "FlowBridgeTest@2026"
USERNAMES = ("team-admin", "tester01", "tester02", "tester03")
DEPARTMENT_ID = uuid5(NAMESPACE_URL, "flowbridge/team-test/v1/department")
DEPARTMENT_CODE = "TEAM-TEST"


class TeamSetupError(Exception):
    pass


def subject(username: str) -> str:
    return str(uuid5(NAMESPACE_URL, "flowbridge/team-test/v1/users/" + username))


def require_local(settings: Settings) -> str:
    issuer = urlsplit(settings.oidc_issuer)
    if (
        settings.environment == "production"
        or issuer.scheme != "https"
        or issuer.hostname != "localhost"
        or issuer.path != "/realms/flowbridge"
        or issuer.username
        or issuer.password
        or issuer.query
        or issuer.fragment
    ):
        raise TeamSetupError("Team test accounts require the local development Keycloak profile")
    base = f"{issuer.scheme}://{issuer.netloc}"
    if settings.oidc_admin_url != base + "/admin/realms/flowbridge":
        raise TeamSetupError("Directory and issuer must belong to the same local realm")
    return base


async def provision_keycloak(
    http: httpx.AsyncClient, base: str, operator: dict, password: str = PASSWORD
) -> None:
    response = await http.post(
        base + "/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": operator["bootstrap_admin"],
            "password": operator["bootstrap_password"],
        },
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    realm = base + "/admin/realms/flowbridge"
    missing = []
    # Preflight every name before mutation; do not replace an unrelated user or reset credentials.
    for username in USERNAMES:
        result = await http.get(
            realm + "/users", params={"username": username, "exact": "true"}, headers=headers
        )
        result.raise_for_status()
        users = result.json()
        if users:
            if (
                len(users) != 1
                or users[0].get("id") != subject(username)
                or users[0].get("username") != username
                or users[0].get("enabled") is not True
                or users[0].get("serviceAccountClientId") is not None
            ):
                raise TeamSetupError("Conflicting or disabled Keycloak user: " + username)
        else:
            result = await http.get(realm + "/users/" + subject(username), headers=headers)
            if result.status_code != 404:
                result.raise_for_status()
                raise TeamSetupError("Reserved test ID is already in use: " + username)
            missing.append(
                {
                    "id": subject(username),
                    "username": username,
                    "firstName": username,
                    "lastName": "Team test",
                    "email": username + "@example.invalid",
                    "emailVerified": True,
                    "enabled": True,
                    "requiredActions": [],
                    "credentials": [{"type": "password", "value": password, "temporary": False}],
                }
            )
    if missing:
        result = await http.post(
            realm + "/partialImport",
            headers=headers,
            json={"ifResourceExists": "SKIP", "users": missing},
        )
        result.raise_for_status()
    # Verify upstream actually preserved the stable IDs before linking any database user.
    for username in USERNAMES:
        result = await http.get(realm + "/users/" + subject(username), headers=headers)
        result.raise_for_status()
        user = result.json()
        if user.get("username") != username or user.get("enabled") is not True:
            raise TeamSetupError("Keycloak identity verification failed: " + username)


async def select_admin(
    repo: IdentityAccountRepository,
    issuer: str,
    operator: dict,
    granted_by: UUID | None,
) -> UUID | None:
    admins = await repo.active_admins()
    if granted_by is not None:
        if granted_by not in admins:
            raise TeamSetupError("--granted-by must identify an active application ADMIN")
        return granted_by
    if not admins:
        return None
    # Reuse only the known team admin or the explicitly configured local profile admin.
    references = [subject("team-admin")]
    local = operator.get("test_users", {}).get("admin", {}).get("id")
    if local:
        references.append(local)
    for reference in references:
        user = await repo.find_identity(issuer, reference)
        if user and user["user_id"] in admins:
            return user["user_id"]
    raise TeamSetupError(
        "This DB already has another ADMIN. Rerun with --granted-by APPLICATION_ADMIN_UUID"
    )


async def ensure_department(session: AsyncSession) -> None:
    table = Department.__table__
    result = await session.execute(
        sa.select(table).where(
            sa.or_(
                table.c.department_id == DEPARTMENT_ID, table.c.department_code == DEPARTMENT_CODE
            )
        )
    )
    rows = result.mappings().all()
    if rows:
        if (
            len(rows) != 1
            or rows[0]["department_id"] != DEPARTMENT_ID
            or rows[0]["department_code"] != DEPARTMENT_CODE
            or rows[0]["status"] != "active"
        ):
            raise TeamSetupError("Conflicting or inactive TEAM-TEST department")
        return
    now = datetime.now(UTC)
    await session.execute(
        sa.insert(table).values(
            department_id=DEPARTMENT_ID,
            department_code=DEPARTMENT_CODE,
            department_name="FlowBridge team test",
            status="active",
            created_at=now,
            updated_at=now,
        )
    )


async def setup(args: argparse.Namespace) -> dict[str, Any]:
    runtime = json.loads((args.identity_dir / "runtime.json").read_text("utf-8"))
    settings = Settings(_env_file=None, **runtime)
    base = require_local(settings)
    if settings.database_url is None:
        raise TeamSetupError("Configure the application database first")
    fixture_path = args.identity_dir / "fixture.json"
    fixture_users = (
        set(json.loads(fixture_path.read_text("utf-8"))["users"].values())
        if fixture_path.exists()
        else set()
    )
    operator = json.loads((args.identity_dir / "operator.json").read_text("utf-8"))
    engine = create_engine(settings, use_null_pool=True)
    try:
        async with (
            httpx.AsyncClient(
                verify=ssl.create_default_context(cafile=settings.oidc_ca_bundle),
                timeout=settings.http_timeout_seconds,
                trust_env=False,
                follow_redirects=False,
            ) as http,
            create_session_factory(engine)() as session,
            session.begin(),
        ):
            # Existing bootstrap uses the same transaction-scoped lock across all machines.
            repo = IdentityAccountRepository(session)
            actor = await select_admin(repo, settings.oidc_issuer, operator, args.granted_by)
            await ensure_department(session)
            for username in USERNAMES:
                existing = await repo.find_identity(settings.oidc_issuer, subject(username))
                if existing and str(existing["user_id"]) in fixture_users:
                    raise TeamSetupError("Team account overlaps an E2E fixture: " + username)
                if existing and (
                    existing["status"] != "active"
                    or existing["account_type"] != "human"
                    or existing["department_id"] != DEPARTMENT_ID
                ):
                    raise TeamSetupError("Conflicting application user: " + username)
            password = os.environ.get("FLOWBRIDGE_TEAM_TEST_PASSWORD", PASSWORD)
            if not password.strip():
                raise TeamSetupError("Team test password must not be empty")
            await provision_keycloak(http, base, operator, password)
            directory = KeycloakDirectory(http, settings)
            results = {}
            for username in USERNAMES:
                identity = await directory.resolve(subject(username))
                result = await bootstrap_identity(
                    repo,
                    identity,
                    roles=["ADMIN"] if username == "team-admin" else ["EMPLOYEE"],
                    department_id=DEPARTMENT_ID,
                    granted_by=actor,
                    first_admin=actor is None and username == "team-admin",
                )
                if username == "team-admin":
                    actor = UUID(result["user_id"])
                results[username] = result
        return {"department_id": str(DEPARTMENT_ID), "users": results}
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the documented local team test accounts")
    parser.add_argument("--identity-dir", type=Path, required=True)
    parser.add_argument("--granted-by", type=UUID)
    args = parser.parse_args()
    try:
        result = asyncio.run(setup(args))
    except TeamSetupError as exc:
        print("Team setup stopped: " + str(exc))
        return 1
    except Exception as exc:
        # IAM creation and SQL commit are separate: an interrupted setup can be rerun safely.
        print(
            f"Team setup failed ({type(exc).__name__}). "
            "Database changes rolled back if uncommitted; "
            "created Keycloak users may remain. Inspect services and rerun the same command."
        )
        return 1
    print(json.dumps(result))
    print("Team accounts ready. Use the passwords documented in HUONG-DAN-SU-DUNG-BACKEND.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
