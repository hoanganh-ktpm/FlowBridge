# SPDX-License-Identifier: GPL-3.0-or-later
"""Real asyncpg/pgvector adapter checks in a rollback-only outer transaction.

Requires an explicitly configured PostgreSQL test database with migration 0003.
Embedding vectors are controlled; these tests do not benchmark model accuracy.
"""

from dataclasses import replace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from flowbridge.db import create_engine
from flowbridge.settings import Settings
from flowbridge.supplier_assistant.knowledge.models import KnowledgeError, RetrievalRequest
from flowbridge.supplier_assistant.knowledge.postgres_domain import PgScope, prepare_chunks
from flowbridge.supplier_assistant.knowledge.postgres_service import PgKnowledgeService
from flowbridge.supplier_assistant.knowledge.postgres_store import PgKnowledgeStore
from flowbridge.supplier_assistant.knowledge.sources import sha256

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class BasisEmbedder:
    profile = "integration:normalized:1024:revision1"
    calls = 0

    def query(self, text):
        return [1.0] + [0.0] * 1023

    def documents(self, texts):
        self.calls += 1
        return [[1.0] + [0.0] * 1023 for _ in texts]


@pytest_asyncio.fixture
async def pg_case():
    settings = Settings()
    assert settings.database_url is not None, "Configure an isolated PostgreSQL test database"
    engine = create_engine(settings, use_null_pool=True)
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            sessions = async_sessionmaker(
                connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            try:
                store = PgKnowledgeStore(sessions)
                await store.verify_schema()
                document_id, supplier_id = uuid4(), uuid4()
                body = "# Nhà cung cấp\n\n## Giao hàng\nPO TEST-001 giao trễ 26 ngày.\n"
                async with sessions() as session, session.begin():
                    await session.execute(
                        text("""
                        INSERT INTO core.documents(document_id,document_kind,subject_type,
                          subject_id,title,scope_type,owner_subject)
                        VALUES(:id,'supplier_review','supplier',:supplier,'Integration',
                          'global','operator://integration')
                    """),
                        {"id": document_id, "supplier": str(supplier_id)},
                    )
                    await session.execute(
                        text("""
                        INSERT INTO core.document_versions(document_id,version_no,title_snapshot,
                          content_md,checksum_sha256,status,metadata,created_by_subject)
                        VALUES(:id,1,'Integration',:body,:hash,'published',
                          jsonb_build_object('supplier_id',CAST(:supplier AS text),
                            'reviewer_user_id',CAST(:reviewer AS text),
                            'reviewer_name_snapshot','Integration reviewer',
                            'report_asof','2026-10-01T00:00:00Z'),
                          'operator://integration')
                    """),
                        {
                            "id": document_id,
                            "supplier": str(supplier_id),
                            "reviewer": str(uuid4()),
                            "body": body,
                            "hash": sha256(body),
                        },
                    )
                    cutoff = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
                scope_value = PgScope(cutoff, supplier_id=supplier_id, operator=True)

                async def scope():
                    return scope_value

                version = (await store.versions(scope_value))[0]
                embedder = BasisEmbedder()
                service = PgKnowledgeService(store, embedder)
                yield store, service, version, scope, sessions, embedder
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


async def test_real_index_retry_search_and_reference_resolution(pg_case):
    store, service, version, scope, sessions, embedder = pg_case
    first = await service.index_versions([version], scope)
    second = await service.index_versions([version], scope)
    assert first == second and embedder.calls == 1
    response = await service.search(RetrievalRequest(query="giao trễ 26 ngày", top_k=1), scope)
    assert "26 ngày" in response["chunks"][0]["text"]
    ref = response["chunks"][0]["reference"]
    resolved = await service.resolve(ref, scope)
    assert resolved["text"] == response["chunks"][0]["text"]


async def test_real_failed_batch_rolls_back_previous_generation(pg_case):
    store, service, version, scope, sessions, embedder = pg_case
    await service.index_versions([version], scope)
    chunks = prepare_chunks(version, embedder.profile)
    corrupt = [replace(chunks[0], content="forged quotation"), *chunks[1:]]
    with pytest.raises(DBAPIError):
        await store.write_generation(
            version,
            corrupt,
            embedder.documents([c.content for c in corrupt]),
            embedder.profile,
            await scope(),
        )
    response = await service.search(RetrievalRequest(query="giao trễ 26 ngày"), scope)
    assert response["chunks"]


async def test_real_profile_rebuild_retains_old_reference(pg_case):
    store, service, version, scope, sessions, embedder = pg_case
    await service.index_versions([version], scope)
    old = prepare_chunks(version, embedder.profile)[0].record.reference.model_dump(mode="json")
    embedder.profile += ":revision2"
    await service.index_versions([version], scope)
    assert (await service.resolve(old, scope))["reference"] == old


async def test_real_deleted_source_is_immediately_excluded(pg_case):
    store, service, version, scope, sessions, embedder = pg_case
    await service.index_versions([version], scope)
    old = prepare_chunks(version, embedder.profile)[0].record.reference.model_dump(mode="json")
    async with sessions() as session, session.begin():
        await session.execute(
            text("UPDATE core.documents SET deleted_at=now() WHERE document_id=:id"),
            {"id": version.document_id},
        )
    assert not (await service.search(RetrievalRequest(query="giao trễ"), scope))["chunks"]
    with pytest.raises(KnowledgeError):
        await service.resolve(old, scope)
