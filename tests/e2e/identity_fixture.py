# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Fixture setup for real browser + IdP + PostgreSQL tests; never overrides API authentication.
import argparse
import asyncio
import json
import os
import ssl
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from flowbridge.db import create_engine, create_session_factory
from flowbridge.identity.account_repository import IdentityAccountRepository
from flowbridge.identity.accounts import IdentityAccounts
from flowbridge.identity.directory import KeycloakDirectory
from flowbridge.settings import Settings
from flowbridge.supplier_assistant.repository import SupplierRepository, table

ROOT = Path(__file__).resolve().parents[2]


async def run(state: Path, cleanup: bool) -> None:
    settings = Settings(
        _env_file=ROOT / ".env", **json.loads((state / "runtime.json").read_text("utf-8"))
    )
    engine = create_engine(settings, use_null_pool=True)
    sessions = create_session_factory(engine)
    fixture_path = state / "fixture.json"
    try:
        if cleanup:
            fixture = json.loads(fixture_path.read_text("utf-8"))
            user_ids = [UUID(value) for value in fixture["users"].values()]
            requests = sa.select(table("purchase_requests").c.request_id).where(
                table("purchase_requests").c.requested_by.in_(user_ids)
            )
            async with sessions() as session, session.begin():
                running = await session.scalar(
                    sa.select(
                        sa.exists().where(
                            table("chat_turns").c.owner_id.in_(user_ids),
                            table("chat_turns").c.state == "running",
                        )
                    )
                )
                if running:
                    raise RuntimeError("Chat execution unresolved; preserve fixture for recovery")
                await session.execute(
                    sa.delete(table("chat_turns")).where(
                        table("chat_turns").c.owner_id.in_(user_ids)
                    )
                )
                await session.execute(
                    sa.delete(table("chat_sessions")).where(
                        table("chat_sessions").c.owner_id.in_(user_ids)
                    )
                )
                await session.execute(
                    sa.delete(table("api_idempotency")).where(
                        sa.or_(
                            *[
                                table("api_idempotency").c.scope.like(str(user) + ":%")
                                for user in user_ids
                            ]
                        )
                    )
                )
                await session.execute(
                    sa.delete(table("outbox_events")).where(
                        sa.or_(
                            table("outbox_events").c.aggregate_id.in_(requests),
                            table("outbox_events").c.aggregate_id.in_(user_ids),
                        )
                    )
                )
                await session.execute(
                    sa.delete(table("purchase_request_items")).where(
                        table("purchase_request_items").c.request_id.in_(requests)
                    )
                )
                await session.execute(
                    sa.delete(table("purchase_requests")).where(
                        table("purchase_requests").c.requested_by.in_(user_ids)
                    )
                )
                await session.execute(
                    sa.delete(table("user_roles")).where(
                        table("user_roles").c.user_id.in_(user_ids)
                    )
                )
                await session.execute(
                    sa.delete(table("users")).where(table("users").c.user_id.in_(user_ids))
                )
                await session.execute(
                    sa.delete(table("departments")).where(
                        table("departments").c.department_id == UUID(fixture["department_id"])
                    )
                )
            # NullPool gives a new DB connection after the cleanup transaction committed.
            # Retain the manifest if verification fails so recovery can safely retry.
            async with engine.connect() as verification:
                await verification.execute(sa.text("SET TRANSACTION READ ONLY"))
                for statement in (
                    sa.select(sa.exists().where(table("users").c.user_id.in_(user_ids))),
                    sa.select(
                        sa.exists().where(
                            table("departments").c.department_id == UUID(fixture["department_id"])
                        )
                    ),
                    sa.select(
                        sa.exists().where(table("purchase_requests").c.requested_by.in_(user_ids))
                    ),
                ):
                    assert not (await verification.execute(statement)).scalar_one(), (
                        "Fixture cleanup verification failed; preserve the manifest"
                    )
            fixture_path.unlink()
            print("SSO browser fixture cleanup complete.")
            return
        if fixture_path.exists():
            raise RuntimeError("Existing fixture needs cleanup before preparing another run")
        operator = json.loads((state / "operator.json").read_text("utf-8"))
        assert operator["test_users"], "Prepare a local identity profile with --test-users"
        gateway_profile = False
        if (
            os.environ.get("FLOWBRIDGE_GATEWAY_TEST") == "true"
            and os.environ.get("FLOWBRIDGE_DEV_TEST_MODE") == "true"
            and (state / "gateway-profile.json").is_file()
        ):
            metadata = json.loads((state / "dev-profile.json").read_text("utf-8"))
            gateway_profile = (
                metadata.get("issuer_host") == "identity"
                and metadata.get("lifespan") == 20
                and settings.oidc_issuer
                == (f"https://identity:{metadata['iam']}/realms/flowbridge")
            )
        assert settings.oidc_issuer.startswith("https://localhost:") or gateway_profile, (
            "Fixture is for local IAM only"
        )
        async with httpx.AsyncClient(
            verify=ssl.create_default_context(cafile=settings.oidc_ca_bundle),
            trust_env=False,
            timeout=20,
        ) as http:
            directory = KeycloakDirectory(http, settings)
            identities = {
                code: await directory.resolve(account["id"])
                for code, account in operator["test_users"].items()
            }
            for code in ("ai", "po"):
                client_id = "flowbridge-" + code
                token_response = await http.post(
                    settings.oidc_issuer + "/protocol/openid-connect/token",
                    data={"grant_type": "client_credentials"},
                    auth=(client_id, operator["service_secrets"][client_id]),
                )
                token_response.raise_for_status()
                inspection = await http.post(
                    settings.oidc_introspection_url,
                    data={
                        "token": token_response.json()["access_token"],
                        "token_type_hint": "access_token",
                    },
                    auth=(settings.oidc_client_id, settings.oidc_client_secret.get_secret_value()),
                )
                inspection.raise_for_status()
                claims = inspection.json()
                assert claims["active"] is True and claims["client_id"] == client_id
                identities[code] = await directory.resolve(claims["sub"])
                assert identities[code].account_type == "service"
        dept_id = uuid4()
        async with sessions() as session, session.begin():
            repo = SupplierRepository(session)
            versions = (
                (await session.execute(sa.text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
            heads = set(ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads())
            assert heads and set(versions) == heads, "DB revision differs from source head"
            timestamp = datetime.now(UTC)
            await repo.insert(
                "departments",
                {
                    "department_id": dept_id,
                    "department_code": "SSO-" + dept_id.hex,
                    "department_name": "Isolated SSO test",
                    "status": "active",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
            users = {}
            accounts = IdentityAccounts(IdentityAccountRepository(repo.session))
            for code, identity in identities.items():
                existing = await repo.rows(
                    "users",
                    table("users").c.external_issuer == identity.issuer,
                    table("users").c.external_subject == identity.subject,
                )
                assert not existing, "Refusing to reuse/delete an existing application user"
                user, _ = await accounts.link(
                    identity, display_name="SSO test " + code, department_id=dept_id
                )
                users[code] = user
            for code, user in users.items():
                await accounts.grant(
                    user,
                    {"admin": "ADMIN", "ai": "AI_SERVICE", "po": "PO_SERVICE"}.get(
                        code, "EMPLOYEE"
                    ),
                    department_id=dept_id,
                    granted_by=users["admin"]["user_id"],
                )
            product = await repo.one("products", table("products").c.status == "active")
            fixture = {
                "department_id": str(dept_id),
                "users": {k: str(v["user_id"]) for k, v in users.items()},
                "product_id": str(product["product_id"]),
            }
            fixture_path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        print("Prepared isolated SSO browser fixture; actual IdP identities verified.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Prepare/clean only this browser test's DB fixture"
    )
    parser.add_argument("--identity-dir", required=True, type=Path)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.identity_dir.resolve(), args.cleanup))
    except Exception as error:
        print(f"SSO fixture failed ({type(error).__name__}); inspect state before retry")
        raise SystemExit(1) from None
