# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Real IAM + SQL service principal; revocation tests use injected verified human metadata only.
import json
import os
import ssl
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from starlette.requests import Request

from flowbridge.db import create_engine, create_session_factory
from flowbridge.identity.authentication import VerifiedIdentity, verify_access_token
from flowbridge.identity.directory import KeycloakDirectory
from flowbridge.identity.repository import IdentityRepository
from flowbridge.identity.service import IdentityService
from flowbridge.models import Role, User, UserRole
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_live_principal_revocation_and_service_client_binding() -> None:
    root = Path(__file__).resolve().parents[2]
    state = Path(os.environ["FLOWBRIDGE_SSO_IDENTITY_DIR"]).resolve()
    runtime = json.loads((state / "runtime.json").read_text("utf-8"))
    fixture = json.loads((state / "fixture.json").read_text("utf-8"))
    operator = json.loads((state / "operator.json").read_text("utf-8"))
    config = Settings(_env_file=root / ".env", **runtime)
    assert config.core_identity is not None
    assert config.oidc_issuer.startswith("https://localhost:")
    assert fixture["users"]["employee"] != fixture["users"]["admin"]
    engine = create_engine(config, use_null_pool=True)
    try:
        async with httpx.AsyncClient(
            verify=ssl.create_default_context(cafile=config.oidc_ca_bundle),
            trust_env=False,
            timeout=config.http_timeout_seconds,
        ) as http:
            directory = KeycloakDirectory(http, config)
            sessions = create_session_factory(engine)
            async with sessions() as session:
                transaction = await session.begin()
                try:
                    service = IdentityService(config, IdentityRepository(session), directory)
                    human = VerifiedIdentity(
                        config.oidc_issuer,
                        operator["test_users"]["employee"]["id"],
                        config.core_identity.web_client_id,
                        "human",
                        frozenset({"identity:read"}),
                    )
                    principal = await service.resolve(
                        human, kind="human", permission="identity:read"
                    )
                    assert principal.principal.id == fixture["users"]["employee"]
                    assert principal.principal.tenant_id == config.core_identity.tenant_id
                    # Obtain and introspect a real service token, not injected metadata.
                    token_response = await http.post(
                        config.oidc_issuer + "/protocol/openid-connect/token",
                        auth=("flowbridge-ai", operator["service_secrets"]["flowbridge-ai"]),
                        data={"grant_type": "client_credentials"},
                    )
                    assert token_response.status_code == 200
                    request = Request(
                        {
                            "type": "http",
                            "headers": [],
                            "app": SimpleNamespace(
                                state=SimpleNamespace(
                                    settings=config, resources=SimpleNamespace(http=http)
                                ),
                            ),
                        }
                    )
                    identity = await verify_access_token(
                        request, token_response.json()["access_token"]
                    )
                    actor = await service.resolve(
                        identity, kind="service", permission="identity:read"
                    )
                    assert actor.principal.id == fixture["users"]["ai"]
                    assert actor.client_id == "flowbridge-ai"
                    assert actor.account_type == "service"
                    # Revoke only this isolated fixture's grant; all SQL changes roll back.
                    await session.execute(
                        sa.update(UserRole.__table__)
                        .where(
                            UserRole.__table__.c.user_id == UUID(fixture["users"]["employee"]),
                            UserRole.__table__.c.revoked_at.is_(None),
                        )
                        .values(
                            revoked_at=datetime.now(UTC), revoked_by=UUID(fixture["users"]["admin"])
                        )
                    )
                    with pytest.raises(CoreError) as failure:
                        await service.resolve(human, kind="human", permission="identity:read")
                    assert failure.value.code == ErrorCode.FORBIDDEN
                    await session.execute(
                        sa.update(User.__table__)
                        .where(
                            User.__table__.c.user_id == UUID(fixture["users"]["ai"]),
                        )
                        .values(status="disabled")
                    )
                    with pytest.raises(CoreError):
                        await service.resolve(identity, kind="service", permission="identity:read")
                finally:
                    await transaction.rollback()
            # Separate transaction proves rollback restores active identity/grant.
            async with sessions() as session, session.begin():
                service = IdentityService(config, IdentityRepository(session), directory)
                assert await service.resolve(human, kind="human", permission="identity:read")
                roles = Role.__table__
                await session.execute(
                    sa.update(roles)
                    .where(
                        roles.c.role_code == "EMPLOYEE",
                    )
                    .values(status="disabled")
                )
                with pytest.raises(CoreError):
                    await service.resolve(human, kind="human", permission="identity:read")
                # No committed modifications to shared roles, even if an assertion fails.
                await session.rollback()
    finally:
        await engine.dispose()
