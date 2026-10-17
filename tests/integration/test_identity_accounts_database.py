# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Scope-specific SQL acceptance; no migration, seed or dependency on application test fixtures.
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from flowbridge.db import create_engine, create_session_factory
from flowbridge.identity.account_repository import IdentityAccountRepository
from flowbridge.identity.accounts import IdentityAccounts
from flowbridge.identity.audit import IdentityOutbox
from flowbridge.identity.bootstrap import bootstrap_identity
from flowbridge.identity.directory import DirectoryIdentity
from flowbridge.models import Department, OutboxEvent, Role, User, UserRole
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_identity_link_bootstrap_retry_authorization_and_atomic_rollback() -> None:
    settings = Settings()
    assert settings.database_url is not None, "Integration requires configured PostgreSQL"
    engine = create_engine(settings, use_null_pool=True)
    namespace, department_id = uuid4().hex, uuid4()
    timestamp = datetime.now(UTC)
    issuer = "https://integration.invalid/identity-review/" + namespace
    try:
        async with create_session_factory(engine)() as session:
            transaction = await session.begin()
            try:
                # Resolve every column used by this adapter before writing the isolated fixture.
                for model in (Department, User, Role, UserRole, OutboxEvent):
                    await session.execute(sa.select(model.__table__).limit(0))
                await session.execute(
                    sa.insert(Department.__table__).values(
                        department_id=department_id,
                        department_code="REVIEW-" + namespace,
                        department_name="Isolated Identity review",
                        status="active",
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                repo = IdentityAccountRepository(session)
                accounts = IdentityAccounts(repo)
                admin, _ = await accounts.link(
                    DirectoryIdentity(issuer, "admin", "human", "Review admin", None),
                    display_name="Review admin",
                )
                await accounts.grant(
                    admin, "ADMIN", department_id=None, granted_by=admin["user_id"]
                )
                identity = DirectoryIdentity(issuer, "employee", "human", "Review employee", None)
                args = dict(
                    roles=["EMPLOYEE"], department_id=department_id, granted_by=admin["user_id"]
                )
                first = await bootstrap_identity(repo, identity, **args)
                again = await bootstrap_identity(
                    repo, identity, display_name="must not overwrite", **args
                )
                assert first["created"] and first["grants_added"] == 1
                assert again == {"user_id": first["user_id"], "created": False, "grants_added": 0}
                user = await repo.find_identity(issuer, "employee")
                assert user["display_name"] == "Review employee"
                user_id = UUID(first["user_id"])

                async def count(model: type, identifier: UUID) -> int:
                    column = model.__table__.c[
                        "aggregate_id" if model is OutboxEvent else "user_id"
                    ]
                    return (
                        await session.execute(
                            sa.select(sa.func.count())
                            .select_from(model.__table__)
                            .where(column == identifier)
                        )
                    ).scalar_one()

                assert await count(UserRole, user_id) == 1
                assert await count(OutboxEvent, user_id) == 1
                event = (
                    (
                        await session.execute(
                            sa.select(OutboxEvent.__table__).where(
                                OutboxEvent.__table__.c.aggregate_id == user_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert event["event_type"] == "identity.bootstrap_applied"
                assert event["status"] == "pending" and event["attempt_count"] == 0
                assert event["payload"] == {
                    "user_id": str(user_id),
                    "actor_id": str(admin["user_id"]),
                    "roles": ["EMPLOYEE"],
                }
                for kwargs in (
                    dict(roles=["ADMIN"], first_admin=True),
                    dict(roles=["ADMIN"], granted_by=user_id),
                ):
                    with pytest.raises(CoreError) as denied:
                        await bootstrap_identity(repo, identity, **kwargs)
                    assert denied.value.code == ErrorCode.FORBIDDEN

                async with session.begin_nested() as dry_run:
                    dry_identity = DirectoryIdentity(issuer, "dry-run", "human", "Dry run", None)
                    result = await bootstrap_identity(repo, dry_identity, **args)
                    dry_id = UUID(result["user_id"])
                    assert await count(OutboxEvent, dry_id) == 1
                    await dry_run.rollback()
                for model in (User, UserRole, OutboxEvent):
                    assert await count(model, dry_id) == 0

                class FailingAudit(IdentityOutbox):
                    user_id: UUID

                    async def bootstrap_applied(
                        self, user_id: UUID, actor_id: UUID, roles: list[str]
                    ) -> None:
                        await super().bootstrap_applied(user_id, actor_id, roles)
                        self.user_id = user_id
                        raise RuntimeError("Injected audit failure")

                audit = FailingAudit(session)
                with pytest.raises(RuntimeError, match="Injected audit failure"):
                    async with session.begin_nested():
                        await bootstrap_identity(
                            repo,
                            DirectoryIdentity(issuer, "audit-failure", "human", "Failure", None),
                            audit=audit,
                            **args,
                        )
                for model in (User, UserRole, OutboxEvent):
                    assert await count(model, audit.user_id) == 0
            finally:
                await transaction.rollback()
        # Verify cleanup from a fresh connection, beyond the transaction under test.
        async with engine.connect() as connection:
            for model, condition in (
                (User, User.__table__.c.external_issuer == issuer),
                (Department, Department.__table__.c.department_id == department_id),
            ):
                assert (
                    await connection.execute(
                        sa.select(sa.func.count()).select_from(model.__table__).where(condition)
                    )
                ).scalar_one() == 0
    finally:
        await engine.dispose()
