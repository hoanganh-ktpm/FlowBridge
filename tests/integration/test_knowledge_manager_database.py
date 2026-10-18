# SPDX-License-Identifier: GPL-3.0-or-later
"""Real PostgreSQL/pgvector, rollback-only sources and migration, controlled embeddings.

The service tests construct a verified identity from an existing live ADMIN grant;
they do not claim to test IAM. HTTP authentication is tested separately.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from flowbridge.db import create_engine
from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.knowledge.manager import KnowledgeManager, row
from flowbridge.supplier_assistant.knowledge.manager_models import Create
from flowbridge.supplier_assistant.knowledge.models import RetrievalRequest
from flowbridge.supplier_assistant.knowledge.postgres_domain import PgScope
from flowbridge.supplier_assistant.knowledge.postgres_service import PgKnowledgeService
from flowbridge.supplier_assistant.knowledge.postgres_store import PgKnowledgeStore
from flowbridge.supplier_assistant.repository import SupplierRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


class Embedder:
    profile = "manager-integration:1024:normalized:v1"
    calls = 0
    fail = False

    def documents(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("controlled embedding failure")
        return [[1.0] + [0.0] * 1023 for _ in texts]

    def query(self, query):
        return [1.0] + [0.0] * 1023


@pytest_asyncio.fixture
async def case():
    engine = create_engine(Settings(), use_null_pool=True)
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            sessions = async_sessionmaker(
                connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            try:
                exists = (
                    await connection.execute(
                        text("SELECT to_regclass('core.knowledge_jobs') IS NOT NULL")
                    )
                ).scalar_one()
                if not exists:
                    sql = (ROOT / "alembic/sql/core_0004.sql").read_text("utf-8")
                    await connection.run_sync(
                        lambda c: c.connection.dbapi_connection.run_async(
                            lambda driver: driver.execute(sql)
                        )
                    )
                async with sessions() as s, s.begin():
                    actor = await row(
                        s,
                        """
                        SELECT u.* FROM public.users u JOIN public.user_roles g USING(user_id)
                          JOIN public.roles r USING(role_id)
                        WHERE u.status='active' AND u.account_type='human' AND r.role_code='ADMIN'
                          AND r.status='active' AND g.revoked_at IS NULL LIMIT 1
                    """,
                    )
                    assert actor, "Requires an existing test ADMIN grant"
                    template = dict(await row(s, "SELECT * FROM public.suppliers LIMIT 1"))
                    suppliers = []
                    for _ in range(2):
                        identifier = uuid4()
                        data = {
                            **template,
                            "supplier_id": identifier,
                            "supplier_code": "KM-" + identifier.hex,
                            "supplier_name": "Rollback-only supplier",
                        }
                        await SupplierRepository(s).insert("suppliers", data)
                        suppliers.append(identifier)
                identity = VerifiedIdentity(actor["external_issuer"], actor["external_subject"])
                embedder = Embedder()
                service = PgKnowledgeService(PgKnowledgeStore(sessions), embedder)
                manager = KnowledgeManager(sessions, service)
                yield manager, identity, actor["user_id"], suppliers, sessions, embedder
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


def payload(actor, supplier, name="test.md", content="* Test\n\nALPHA-ONE"):
    return Create(
        filename=name,
        title="Rollback test",
        content_md=content,
        supplier_id=supplier,
        reviewer_user_id=actor,
        report_asof=datetime.now(UTC),
    ).model_dump(mode="json")


async def finish(manager, sessions, job):
    async with sessions() as s, s.begin():
        claimed = await row(
            s,
            """
            UPDATE core.knowledge_jobs SET status='processing',lease_id=:lease,
              lease_until=clock_timestamp()+interval '2 minutes' WHERE job_id=:id RETURNING *
        """,
            id=job["job_id"],
            lease=uuid4(),
        )
    await manager.process(claimed)
    return await manager.job(VerifiedIdentity(**claimed["actor_identity"]), job["job_id"])


async def search(manager, supplier, query):
    stamp = datetime.now(UTC)

    async def scope():
        return PgScope(stamp, supplier_id=supplier, operator=True)

    return await manager.service.search(RetrievalRequest(query=query, supplier_id=supplier), scope)


async def test_create_edit_history_idempotency_delete_and_recreate(case):
    manager, identity, actor, suppliers, sessions, embedder = case
    body = payload(actor, suppliers[0])
    job = await manager.enqueue(identity, "create", "publish", body)
    assert (await manager.enqueue(identity, "create", "publish", body))["job_id"] == job["job_id"]
    await finish(manager, sessions, job)
    doc = job["document_id"]
    a = await search(manager, suppliers[0], "ALPHA-ONE")
    assert "ALPHA-ONE" in a["chunks"][0]["text"]
    old_reference = a["chunks"][0]["reference"]
    edit = {
        "filename": "test.md",
        "title": "Rollback test",
        "expected_revision": 1,
        "content_md": "* Test\n\nBETA-TWO",
    }
    j = await manager.enqueue(identity, "edit", "publish", edit, doc)
    # Pending publication must not hide the already indexed version.
    assert "ALPHA-ONE" in (await search(manager, suppliers[0], "ALPHA-ONE"))["chunks"][0]["text"]
    await finish(manager, sessions, j)
    b = await search(manager, suppliers[0], "BETA-TWO")
    assert all("ALPHA-ONE" not in c["text"] for c in b["chunks"])
    assert "BETA-TWO" in b["chunks"][0]["text"]
    for i in range(3):
        assert (
            await manager.enqueue(
                identity, f"same{i}", "publish", {**edit, "expected_revision": 2}, doc
            )
        )["status"] == "unchanged"
    assert embedder.calls == 2
    detail = await manager.detail(identity, doc)
    assert detail["revision"] == 2 and len(detail["versions"]) == 2
    assert "ALPHA-ONE" in (await manager.history(identity, doc, 1))["content_md"]
    stamp = datetime.now(UTC)

    async def scope():
        return PgScope(stamp, supplier_id=suppliers[0], operator=True)

    assert (await manager.service.resolve(old_reference, scope))["reference"] == old_reference
    delete = await manager.enqueue(identity, "delete", "delete", {"expected_revision": 2}, doc)
    await finish(manager, sessions, delete)
    assert not (await search(manager, suppliers[0], "BETA-TWO"))["chunks"]
    async with sessions() as s:
        assert not await row(
            s, "SELECT 1 FROM core.document_versions WHERE document_id=:id", id=doc
        )
        assert not await row(s, "SELECT 1 FROM core.documents WHERE document_id=:id", id=doc)
    recreated = await manager.enqueue(identity, "recreate", "publish", body)
    assert recreated["document_id"] != doc


async def test_failure_preserves_active_source_retry_force_and_other_document(case):
    manager, identity, actor, suppliers, sessions, embedder = case
    jobs = [
        await manager.enqueue(identity, str(i), "publish", payload(actor, supplier, f"test{i}.md"))
        for i, supplier in enumerate(suppliers)
    ]
    for j in jobs:
        await finish(manager, sessions, j)
    other = await manager.detail(identity, jobs[1]["document_id"])
    edit = {
        "filename": "test0.md",
        "title": "Rollback test",
        "expected_revision": 1,
        "content_md": "* Test\n\nBETA-TWO",
    }
    j = await manager.enqueue(identity, "failure", "publish", edit, jobs[0]["document_id"])
    embedder.fail = True
    with pytest.raises(RuntimeError, match="controlled"):
        await finish(manager, sessions, j)
    assert (await manager.detail(identity, j["document_id"]))["revision"] == 1
    assert "ALPHA-ONE" in (await search(manager, suppliers[0], "ALPHA-ONE"))["chunks"][0]["text"]
    async with sessions() as s, s.begin():
        await s.execute(
            text("UPDATE core.knowledge_jobs SET status='failed' WHERE job_id=:id"),
            {"id": j["job_id"]},
        )
    embedder.fail = False
    retry = await manager.retry(identity, "retry", j["job_id"])
    await finish(manager, sessions, retry)
    assert (await manager.detail(identity, other["document_id"])) == other
    before = embedder.calls
    force = await manager.enqueue(
        identity, "force", "reindex", {"expected_revision": 2}, j["document_id"]
    )
    await finish(manager, sessions, force)
    assert embedder.calls == before + 1


async def test_busy_stale_revision_revocation_and_lease_fencing(case):
    manager, identity, actor, suppliers, sessions, embedder = case
    j = await manager.enqueue(identity, "create", "publish", payload(actor, suppliers[0]))
    with pytest.raises(CoreError) as error:
        await manager.enqueue(
            identity, "busy", "delete", {"expected_revision": 0}, j["document_id"]
        )
    assert error.value.code == ErrorCode.ANALYSIS_IN_PROGRESS
    await finish(manager, sessions, j)
    with pytest.raises(CoreError) as error:
        await manager.enqueue(
            identity, "stale", "delete", {"expected_revision": 0}, j["document_id"]
        )
    assert error.value.code == ErrorCode.VERSION_CONFLICT
    j2 = await manager.enqueue(
        identity, "force", "reindex", {"expected_revision": 1}, j["document_id"]
    )
    async with sessions() as s, s.begin():
        claimed = await row(
            s,
            "UPDATE core.knowledge_jobs SET status='processing',lease_id=:lease "
            "WHERE job_id=:id RETURNING *",
            id=j2["job_id"],
            lease=uuid4(),
        )
    async with sessions() as s, s.begin():
        await s.execute(
            text("UPDATE core.knowledge_jobs SET lease_id=:lease WHERE job_id=:id"),
            {"id": j2["job_id"], "lease": uuid4()},
        )
    with pytest.raises(CoreError) as error:
        await manager.process(claimed)
    assert error.value.code == ErrorCode.LEASE_EXPIRED
    async with sessions() as s, s.begin():
        await s.execute(
            text("UPDATE public.users SET status='disabled' WHERE user_id=:id"), {"id": actor}
        )
    with pytest.raises(CoreError) as error:
        await manager.listing(identity)
    assert error.value.code == ErrorCode.FORBIDDEN


async def test_cited_document_blocks_purge_and_can_be_withdrawn(case):
    manager, identity, actor, suppliers, sessions, embedder = case
    j = await manager.enqueue(identity, "create", "publish", payload(actor, suppliers[0]))
    await finish(manager, sessions, j)
    result = await search(manager, suppliers[0], "ALPHA-ONE")
    ref = result["chunks"][0]["reference"]
    import json

    async with sessions() as s, s.begin():
        await s.execute(
            text("""
            UPDATE public.risk_analysis_reports SET report_data=jsonb_set(
              report_data,'{manager_test_ref}',CAST(:ref AS jsonb))
            WHERE report_id=(SELECT report_id FROM public.risk_analysis_reports LIMIT 1)
        """),
            {"ref": json.dumps(ref)},
        )
    d = await manager.detail(identity, j["document_id"])
    assert d["has_citations"] and not d["can_delete"]
    with pytest.raises(CoreError) as error:
        await manager.enqueue(
            identity, "purge", "delete", {"expected_revision": 1}, j["document_id"]
        )
    assert error.value.code == ErrorCode.INVALID_STATE
    withdrawal = await manager.enqueue(
        identity, "withdraw", "withdraw", {"expected_revision": 1}, j["document_id"]
    )
    await finish(manager, sessions, withdrawal)
    assert not (await search(manager, suppliers[0], "ALPHA-ONE"))["chunks"]
    assert "ALPHA-ONE" in (await manager.history(identity, j["document_id"], 1))["content_md"]


async def test_crash_reclaims_the_same_job_and_duplicate_filename_rolls_back(case):
    manager, identity, actor, suppliers, sessions, embedder = case
    j = await manager.enqueue(identity, "create", "publish", payload(actor, suppliers[0]))
    async with sessions() as s, s.begin():
        await s.execute(
            text("""
            UPDATE core.knowledge_jobs SET status='processing',lease_id=:lease,
              lease_until=clock_timestamp()-interval '1 minute' WHERE job_id=:id
        """),
            {"id": j["job_id"], "lease": uuid4()},
        )
    reclaimed = await manager.claim(j["job_id"])
    assert reclaimed["job_id"] == j["job_id"]
    await manager.process(reclaimed)
    assert (await manager.detail(identity, j["document_id"]))["revision"] == 1
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await manager.enqueue(identity, "duplicate", "publish", payload(actor, suppliers[1]))
    async with sessions() as s:
        assert not await row(
            s,
            "SELECT document_id FROM core.documents WHERE subject_id=:supplier",
            supplier=str(suppliers[1]),
        )


async def test_non_admin_cannot_read_jobs_or_sources(case):
    manager, identity, actor, suppliers, sessions, embedder = case
    async with sessions() as s:
        employee = await row(
            s,
            """
            SELECT u.* FROM public.users u JOIN public.user_roles g USING(user_id)
            JOIN public.roles r USING(role_id) WHERE r.role_code='EMPLOYEE'
              AND u.status='active' AND g.revoked_at IS NULL
              AND NOT EXISTS(SELECT 1 FROM public.user_roles a JOIN public.roles ar USING(role_id)
                WHERE a.user_id=u.user_id AND ar.role_code='ADMIN' AND a.revoked_at IS NULL)
            LIMIT 1
        """,
        )
    assert employee
    unprivileged = VerifiedIdentity(employee["external_issuer"], employee["external_subject"])
    for operation in [
        manager.listing(unprivileged),
        manager.options(unprivileged),
        manager.job(unprivileged, uuid4()),
    ]:
        with pytest.raises(CoreError) as error:
            await operation
        assert error.value.code == ErrorCode.FORBIDDEN


async def test_editing_failed_initial_upload_keeps_validated_bindings(case):
    manager, identity, actor, suppliers, sessions, embedder = case
    j = await manager.enqueue(identity, "create", "publish", payload(actor, suppliers[0]))
    embedder.fail = True
    with pytest.raises(RuntimeError, match="controlled"):
        await finish(manager, sessions, j)
    async with sessions() as s, s.begin():
        await s.execute(
            text("UPDATE core.knowledge_jobs SET status='failed' WHERE job_id=:id"),
            {"id": j["job_id"]},
        )
    embedder.fail = False
    revised = await manager.enqueue(
        identity,
        "fix-draft",
        "publish",
        {
            "filename": "test.md",
            "title": "Corrected upload",
            "expected_revision": 0,
            "content_md": "* Updated\n\nCORRECTED-DRAFT",
        },
        j["document_id"],
    )
    await finish(manager, sessions, revised)
    detail = await manager.detail(identity, j["document_id"])
    assert detail["metadata"]["reviewer_user_id"] == str(actor)
    assert detail["revision"] == 1 and detail["index_status"] == "ready"
