# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Select existing AI identity/job; never provision, import, index or modify database records.
import asyncio
import json

from langflow_test_backend import PROFILE, create_engine, create_session_factory, local_settings
from sqlalchemy import text

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.supplier_assistant.knowledge.embeddings import QwenEmbedder
from flowbridge.supplier_assistant.knowledge.postgres_domain import (
    prepare_chunks,
    validate_stored_chunks,
)
from flowbridge.supplier_assistant.knowledge.postgres_http import job_scope
from flowbridge.supplier_assistant.knowledge.postgres_store import PgKnowledgeStore
from flowbridge.supplier_assistant.policy import SupplierPolicy
from flowbridge.supplier_assistant.repository import SupplierRepository


async def prepare_rag_profile() -> dict:
    value = json.loads(PROFILE.read_text(encoding="utf-8"))
    settings = local_settings(rag=True)
    engine = create_engine(settings, use_null_pool=True)
    engine.update_execution_options(postgresql_readonly=True)
    sessions = create_session_factory(engine)
    store = PgKnowledgeStore(sessions)
    try:
        await store.verify_schema()
        async with sessions() as session:
            actors = (
                (
                    await session.execute(
                        text("""
                SELECT DISTINCT u.user_id,u.external_issuer,u.external_subject
                FROM users u JOIN user_roles g ON g.user_id=u.user_id
                JOIN roles r ON r.role_id=g.role_id
                WHERE u.status='active' AND u.account_type='service' AND r.role_code='AI_SERVICE'
                AND r.status='active' AND g.revoked_at IS NULL ORDER BY u.user_id LIMIT 100
            """)
                    )
                )
                .mappings()
                .all()
            )
            jobs = (
                (
                    await session.execute(
                        text("""
                SELECT t.task_id FROM analysis_tasks t
                JOIN purchase_requests p ON p.request_id=t.request_id
                JOIN departments d ON d.department_id=p.department_id
                WHERE t.status IN ('queued','running','succeeded') AND p.status<>'cancelled'
                  AND t.input_revision=p.input_revision AND t.feature_asof<=now()
                  AND d.status='active'
                  AND t.feature_asof >= (SELECT min(known_from) FROM core.document_versions
                                        WHERE status='published')
                ORDER BY t.feature_asof DESC,t.task_id LIMIT 100
            """)
                    )
                )
                .scalars()
                .all()
            )
        for actor in actors:
            identity = VerifiedIdentity(
                actor["external_issuer"], actor["external_subject"], account_type="service"
            )
            async with sessions() as session:
                policy = SupplierPolicy(SupplierRepository(session))
                await policy.authenticate(identity)
                if not policy.has("AI_SERVICE"):
                    continue
            for task_id in jobs:
                scope = await job_scope(sessions, identity, task_id)
                versions = await store.versions(scope)
                ready = 0
                for version in versions:
                    expected = prepare_chunks(version, QwenEmbedder.profile)
                    stored = await store.chunks(expected, QwenEmbedder.profile)
                    if validate_stored_chunks(expected, stored):
                        ready += 1
                if not ready:
                    continue
                value.update(
                    ai_user_id=str(actor["user_id"]),
                    job_context=str(task_id),
                    rag_cutoff=scope.as_of.isoformat(),
                    rag_source="postgres",
                )
                PROFILE.write_text(
                    json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n"
                )
                return {
                    "source": "postgres",
                    "indexed_visible_documents": ready,
                    "feature_asof": scope.as_of.isoformat(),
                    "database_writes": 0,
                }
        raise RuntimeError("No valid AI_SERVICE + JobContext with visible indexed documents")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(prepare_rag_profile())))
    except Exception as exc:
        print(f"PostgreSQL RAG preparation failed ({type(exc).__name__}); check role/job/source.")
        raise SystemExit(1) from None
