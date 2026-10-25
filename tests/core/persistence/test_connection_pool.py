# SPDX-License-Identifier: GPL-3.0-or-later
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import text

from app.core.db.engine import core_engine
from app.core.db.postgres import PostgresCoreStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_opt_in_pool_reuses_connections_without_leaking_transaction_mode_or_locks(
    isolated_postgres,
):
    engine = core_engine(
        isolated_postgres["core"].render_as_string(hide_password=False), pool_size=2
    )
    store = PostgresCoreStore(engine)
    try:
        async with engine.connect() as conn:
            backend = await conn.scalar(text("SELECT pg_backend_pid()"))
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT pg_backend_pid()")) == backend
        async with store.transaction(read_only=True) as tx:
            assert await tx.connection.scalar(text("SHOW transaction_read_only")) == "on"
        async with store.transaction() as tx:
            assert await tx.connection.scalar(text("SHOW transaction_read_only")) == "off"
        first, second = uuid4(), uuid4()
        with pytest.raises(RuntimeError, match="caller aborted"):
            async with store.permission_lock(first, second):
                async with store.transaction(read_only=True) as tx:
                    assert await tx.connection.scalar(text("SELECT 1")) == 1
                raise RuntimeError("caller aborted")
        async with engine.connect() as conn:
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() "
                        "AND locktype='advisory'"
                    )
                )
                == 0
            )
            assert await conn.scalar(text("SHOW transaction_read_only")) == "off"
    finally:
        await engine.dispose()


async def test_demo_alias_gets_its_own_membership_and_setup_does_not_reactivate_revocation(
    isolated_postgres,
):
    import sqlalchemy as sa
    from scripts.decision_demo_runtime import ensure_membership

    from app.core.db import models

    engine = sa.create_engine(isolated_postgres["core"], hide_parameters=True)
    org, dept, old, alias = uuid4(), uuid4(), uuid4(), uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                models.departments.insert().values(
                    id=dept,
                    code="demo_" + org.hex,
                    name="Demo regression department",
                    status="ACTIVE",
                )
            )
            for pid in (old, alias):
                conn.execute(
                    models.principals.insert().values(
                        id=pid,
                        issuer="https://identity.example/realms/test",
                        subject=str(pid),
                        kind="human",
                        status="ACTIVE",
                        platform_role="NONE",
                    )
                )
            conn.execute(
                models.department_memberships.insert().values(
                    id=uuid5(org, "membership:coordinator"),
                    principal_id=old,
                    department_id=dept,
                    membership_role="STAFF",
                    status="ACTIVE",
                )
            )
            ensure_membership(conn, org, alias, dept)
            ensure_membership(conn, org, alias, dept)
            rows = (
                conn.execute(
                    sa.select(models.department_memberships).where(
                        models.department_memberships.c.department_id == dept
                    )
                )
                .mappings()
                .all()
            )
            assert {r["principal_id"] for r in rows} == {old, alias}
            assert len(rows) == 2
            conn.execute(
                models.department_memberships.update()
                .where(models.department_memberships.c.principal_id == alias)
                .values(status="REVOKED")
            )
        with engine.begin() as conn:
            with pytest.raises(ValueError, match="no automatic reactivation"):
                ensure_membership(conn, org, alias, dept)
            assert (
                conn.scalar(
                    sa.select(models.department_memberships.c.status).where(
                        models.department_memberships.c.principal_id == alias
                    )
                )
                == "REVOKED"
            )
    finally:
        engine.dispose()
