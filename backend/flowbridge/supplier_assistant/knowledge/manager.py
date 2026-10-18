# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable, per-document operations on the existing Core Markdown/pgvector store."""

import asyncio
import contextlib
import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.policy import SupplierPolicy, require
from flowbridge.supplier_assistant.repository import SupplierRepository

from .models import KnowledgeError
from .postgres_domain import PgScope, PgVersion, prepare_chunks, validate_stored_chunks
from .postgres_store import source_row
from .sources import sha256

log = logging.getLogger(__name__)


async def admin(session, identity, *, lock=False):
    require(identity.account_type == "human", ErrorCode.FORBIDDEN)
    policy = SupplierPolicy(SupplierRepository(session))
    await policy.authenticate(identity)
    policy.authorize("ADMIN")
    if lock:
        # Revocation/disable must serialize with the publication transaction.
        await session.execute(
            text("""
            SELECT u.user_id FROM public.users u JOIN public.user_roles g USING(user_id)
              JOIN public.roles r USING(role_id)
            WHERE u.user_id=:id AND r.role_code='ADMIN' AND g.revoked_at IS NULL
            FOR SHARE OF u,g,r
        """),
            {"id": policy.user["user_id"]},
        )
        await policy.authenticate(identity)
        policy.authorize("ADMIN")
    return policy.user


async def row(session, sql, **params):
    return (await session.execute(text(sql), params)).mappings().one_or_none()


async def document(session, identifier, *, lock=False):
    d = await row(
        session,
        "SELECT * FROM core.documents WHERE document_id=:id" + (" FOR UPDATE" if lock else ""),
        id=identifier,
    )
    require(d is not None, ErrorCode.NOT_FOUND)
    return dict(d)


async def latest(session, identifier):
    v = await row(
        session,
        """
        SELECT * FROM core.document_versions WHERE document_id=:id
        ORDER BY version_no DESC LIMIT 1
    """,
        id=identifier,
    )
    return dict(v) if v else None


def version(d, v):
    return source_row(
        {
            "document_id": d["document_id"],
            "version_id": v["version_id"],
            "version_no": v["version_no"],
            "supplier_id": d["subject_id"],
            "title": v["title_snapshot"],
            "content_md": v["content_md"],
            "checksum": v["checksum_sha256"],
            "metadata": v["metadata"],
            "published_at": v["published_at"],
            "known_from": v["known_from"],
            "status": v["status"],
            "withdrawn_at": v["withdrawn_at"],
        }
    )


def job_public(j):
    return {
        k: j[k]
        for k in (
            "job_id",
            "document_id",
            "kind",
            "status",
            "stage",
            "error_code",
            "result",
            "created_at",
            "updated_at",
        )
    }


class KnowledgeManager:
    def __init__(self, sessions, service, *, max_bytes=2_097_152):
        self.sessions, self.service, self.max_bytes = sessions, service, max_bytes

    async def verify_schema(self):
        async with self.sessions() as s:
            require(
                (
                    await s.execute(
                        text(
                            "SELECT to_regclass('core.knowledge_jobs') IS NOT NULL "
                            "AND to_regclass('core.knowledge_files') IS NOT NULL "
                            "AND to_regprocedure('core.knowledge_is_referenced(uuid)') IS NOT NULL"
                        )
                    )
                ).scalar_one(),
                ErrorCode.UPSTREAM_UNAVAILABLE,
            )

    async def options(self, identity):
        async with self.sessions() as s:
            await admin(s, identity)
            result = {}
            for key, sql in {
                "suppliers": "SELECT s.supplier_id,s.supplier_code,s.supplier_name, "
                "EXISTS(SELECT 1 FROM core.documents d WHERE d.document_kind='supplier_review' "
                "AND d.subject_type='supplier' AND d.subject_id=s.supplier_id::text "
                "AND d.deleted_at IS NULL) AS has_document FROM public.suppliers s "
                "WHERE s.status='active' ORDER BY s.supplier_code",
                "reviewers": "SELECT user_id,display_name FROM public.users "
                "WHERE status='active' AND account_type='human' ORDER BY display_name",
                "departments": "SELECT department_id,department_name FROM public.departments "
                "WHERE status='active' ORDER BY department_name",
            }.items():
                result[key] = [dict(r) for r in (await s.execute(text(sql))).mappings()]
            return result

    async def listing(self, identity):
        async with self.sessions() as s:
            await admin(s, identity)
            ids = (
                (
                    await s.execute(
                        text("""
                SELECT document_id FROM core.documents
                WHERE document_kind='supplier_review' AND subject_type='supplier'
                ORDER BY created_at DESC LIMIT 1000
            """)
                    )
                )
                .scalars()
                .all()
            )
        return {"items": [await self.detail(identity, i, content=False) for i in ids]}

    async def detail(self, identity, identifier, *, content=True):
        async with self.sessions() as s:
            await admin(s, identity)
            d = await document(s, identifier)
            require(d["document_kind"] == "supplier_review", ErrorCode.NOT_FOUND)
            v = await latest(s, identifier)
            name = (
                await s.execute(
                    text("SELECT filename FROM core.knowledge_files WHERE document_id=:id"),
                    {"id": identifier},
                )
            ).scalar_one_or_none()
            j = await row(
                s,
                """
                SELECT * FROM core.knowledge_jobs WHERE document_id=:id
                ORDER BY created_at DESC,job_id DESC LIMIT 1
            """,
                id=identifier,
            )
            cited = (
                await s.execute(
                    text("SELECT core.knowledge_is_referenced(:id)"), {"id": identifier}
                )
            ).scalar_one()
            history = (
                [
                    dict(r)
                    for r in (
                        await s.execute(
                            text("""
                SELECT version_id,version_no,title_snapshot,status,created_by_subject,
                  published_at,known_from,checksum_sha256 FROM core.document_versions
                WHERE document_id=:id ORDER BY version_no DESC
            """),
                            {"id": identifier},
                        )
                    ).mappings()
                ]
                if content
                else []
            )
        chunks = prepare_chunks(version(d, v), self.service.embedder.profile) if v else []
        stored = await self.service.store.chunks(chunks, self.service.embedder.profile)
        ready = bool(chunks) and validate_stored_chunks(chunks, stored)
        state = "ready" if ready else "unindexed"
        if j and j["status"] in {"pending", "processing", "failed"}:
            state = j["status"]
        if d["deleted_at"]:
            state = "withdrawn"
        draft = j["payload"] if j and j["status"] == "failed" else {}
        metadata = v["metadata"] if v else draft.get("metadata", {})
        result = {
            "document_id": identifier,
            "supplier_id": d["subject_id"],
            "supplier_name": metadata.get("supplier_name", d["title"]),
            "filename": name or f"{identifier}.md",
            "title": d["title"],
            "scope_type": d["scope_type"],
            "department_id": d["department_id"],
            "revision": v["version_no"] if v else 0,
            "version_id": v["version_id"] if v else None,
            "content_hash": v["checksum_sha256"] if v else None,
            "index_status": state,
            "chunk_count": len(stored),
            "updated_at": v["known_from"] if v else d["created_at"],
            "can_delete": not cited,
            "has_citations": cited,
            "job": job_public(j) if j else None,
        }
        if content:
            result.update(
                content_md=v["content_md"] if v else draft.get("content_md", ""),
                versions=history,
                metadata=metadata,
            )
        return result

    async def history(self, identity, identifier, revision):
        async with self.sessions() as s:
            await admin(s, identity)
            v = await row(
                s,
                """
                SELECT version_no,title_snapshot,content_md,metadata,published_at,checksum_sha256
                FROM core.document_versions WHERE document_id=:id AND version_no=:revision
            """,
                id=identifier,
                revision=revision,
            )
            require(v is not None, ErrorCode.NOT_FOUND)
            return dict(v)

    async def job(self, identity, identifier):
        async with self.sessions() as s:
            await admin(s, identity)
            j = await row(s, "SELECT * FROM core.knowledge_jobs WHERE job_id=:id", id=identifier)
            require(j is not None, ErrorCode.NOT_FOUND)
            return job_public(j)

    async def enqueue(self, identity, key, kind, payload, identifier=None):
        require(1 <= len(key) <= 160, ErrorCode.VALIDATION_ERROR)
        if "content_md" in payload:
            require(
                len(payload["content_md"].encode("utf-8")) <= self.max_bytes,
                ErrorCode.VALIDATION_ERROR,
            )
        digest = hashlib.sha256(
            json.dumps([kind, str(identifier), payload], sort_keys=True, default=str).encode()
        ).hexdigest()
        async with self.sessions() as s, s.begin():
            await s.execute(text("SET LOCAL lock_timeout='5s'"))
            actor = await admin(s, identity, lock=True)
            await s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"knowledge-manager:{actor['user_id']}:{key}"},
            )
            prior = await row(
                s,
                """
                SELECT * FROM core.knowledge_jobs
                WHERE actor_user_id=:actor AND idempotency_key=:key
            """,
                actor=actor["user_id"],
                key=key,
            )
            if prior:
                require(prior["request_hash"] == digest, ErrorCode.IDEMPOTENCY_CONFLICT)
                return job_public(prior)
            if identifier is None:
                require(kind == "publish", ErrorCode.VALIDATION_ERROR)
                supplier = await row(
                    s,
                    "SELECT * FROM public.suppliers WHERE supplier_id=:id AND status='active'",
                    id=UUID(str(payload["supplier_id"])),
                )
                reviewer = await row(
                    s,
                    "SELECT * FROM public.users WHERE user_id=:id "
                    "AND status='active' AND account_type='human'",
                    id=UUID(str(payload["reviewer_user_id"])),
                )
                require(supplier is not None and reviewer is not None, ErrorCode.VALIDATION_ERROR)
                department = payload.get("department_id")
                if department:
                    require(
                        await row(
                            s,
                            "SELECT department_id FROM public.departments "
                            "WHERE department_id=:id AND status='active'",
                            id=UUID(str(department)),
                        )
                        is not None,
                        ErrorCode.VALIDATION_ERROR,
                    )
                identifier = uuid4()
                await s.execute(
                    text("""
                    INSERT INTO core.documents(document_id,document_kind,subject_type,subject_id,
                      title,scope_type,department_id,owner_subject)
                    VALUES(:id,'supplier_review','supplier',:supplier,:title,:scope,:dept,:owner)
                """),
                    {
                        "id": identifier,
                        "supplier": str(supplier["supplier_id"]),
                        "title": payload["title"],
                        "scope": "department" if department else "global",
                        "dept": UUID(str(department)) if department else None,
                        "owner": str(actor["user_id"]),
                    },
                )
                payload = {
                    **payload,
                    "metadata": {
                        "supplier_id": str(supplier["supplier_id"]),
                        "supplier_code": supplier["supplier_code"],
                        "supplier_name": supplier["supplier_name"],
                        "reviewer_user_id": str(reviewer["user_id"]),
                        "reviewer_name_snapshot": reviewer["display_name"],
                        "report_asof": payload["report_asof"],
                        "reviewer_attribution": "admin_entered_review",
                    },
                }
            d = await document(s, identifier, lock=True)
            require(d["document_kind"] == "supplier_review", ErrorCode.NOT_FOUND)
            require(d["deleted_at"] is None, ErrorCode.INVALID_STATE)
            v = await latest(s, identifier)
            require(
                (v["version_no"] if v else 0) == payload["expected_revision"],
                ErrorCode.VERSION_CONFLICT,
            )
            busy = await row(
                s,
                "SELECT job_id FROM core.knowledge_jobs WHERE document_id=:id "
                "AND status IN ('pending','processing')",
                id=identifier,
            )
            require(busy is None, ErrorCode.ANALYSIS_IN_PROGRESS)
            if kind == "publish":
                if v:
                    payload = {**payload, "metadata": v["metadata"]}
                elif "metadata" not in payload:
                    # A failed first publication has no source version yet. Its
                    # validated supplier/reviewer bindings live in the durable draft.
                    draft = await row(
                        s,
                        """
                        SELECT payload FROM core.knowledge_jobs WHERE document_id=:id
                        AND kind='publish' ORDER BY created_at DESC LIMIT 1
                    """,
                        id=identifier,
                    )
                    require(
                        draft is not None and "metadata" in draft["payload"],
                        ErrorCode.INVALID_STATE,
                    )
                    payload = {**payload, "metadata": draft["payload"]["metadata"]}
                await s.execute(
                    text("""
                    INSERT INTO core.knowledge_files(document_id,filename) VALUES(:id,:name)
                    ON CONFLICT(document_id) DO UPDATE SET filename=EXCLUDED.filename
                """),
                    {"id": identifier, "name": payload["filename"]},
                )
                if (
                    v
                    and v["status"] == "published"
                    and v["content_md"] == payload["content_md"]
                    and v["title_snapshot"] == payload["title"]
                ):
                    return {"status": "unchanged", "document_id": identifier}
                payload = {**payload, "version_id": str(uuid4())}
            elif kind == "reindex":
                require(v is not None and v["status"] == "published", ErrorCode.INVALID_STATE)
            if kind == "delete":
                require(
                    not (
                        await s.execute(
                            text("SELECT core.knowledge_is_referenced(:id)"), {"id": identifier}
                        )
                    ).scalar_one(),
                    ErrorCode.INVALID_STATE,
                )
            j = await row(
                s,
                """
                INSERT INTO core.knowledge_jobs(document_id,kind,expected_revision,actor_user_id,
                  actor_identity,idempotency_key,request_hash,payload)
                VALUES(:id,:kind,:rev,:actor,CAST(:identity AS jsonb),
                  :key,:hash,CAST(:payload AS jsonb))
                RETURNING *
            """,
                id=identifier,
                kind=kind,
                rev=payload["expected_revision"],
                actor=actor["user_id"],
                identity=json.dumps({"issuer": identity.issuer, "subject": identity.subject}),
                key=key,
                hash=digest,
                payload=json.dumps(payload, default=str),
            )
            return job_public(j)

    async def retry(self, identity, key, job_id):
        async with self.sessions() as s:
            await admin(s, identity)
            old = await row(s, "SELECT * FROM core.knowledge_jobs WHERE job_id=:id", id=job_id)
            require(old is not None, ErrorCode.NOT_FOUND)
            require(old["status"] == "failed", ErrorCode.INVALID_STATE)
        return await self.enqueue(identity, key, old["kind"], old["payload"], old["document_id"])

    async def claim(self, job_id=None):
        async with self.sessions() as s, s.begin():
            return await row(
                s,
                """
                UPDATE core.knowledge_jobs SET status='processing',stage='chunking',
                  lease_id=:lease,lease_until=clock_timestamp()+interval '2 minutes',
                  attempts=attempts+1,updated_at=clock_timestamp()
                WHERE job_id=(SELECT job_id FROM core.knowledge_jobs
                  WHERE (status='pending'
                    OR (status='processing' AND lease_until<clock_timestamp()))
                    AND (CAST(:job AS uuid) IS NULL OR job_id=:job)
                  ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *
            """,
                lease=uuid4(),
                job=job_id,
            )

    async def stage(self, j, value):
        async with self.sessions() as s, s.begin():
            r = await s.execute(
                text("""
                UPDATE core.knowledge_jobs SET stage=:stage,updated_at=clock_timestamp(),
                  lease_until=clock_timestamp()+interval '2 minutes'
                WHERE job_id=:id AND lease_id=:lease AND status='processing'
            """),
                {"stage": value, "id": j["job_id"], "lease": j["lease_id"]},
            )
            require(r.rowcount == 1, ErrorCode.LEASE_EXPIRED)

    async def heartbeat(self, j):
        while True:
            await asyncio.sleep(20)
            async with self.sessions() as s, s.begin():
                await s.execute(
                    text("""
                    UPDATE core.knowledge_jobs
                    SET lease_until=clock_timestamp()+interval '2 minutes'
                    WHERE job_id=:id AND lease_id=:lease AND status='processing'
                """),
                    {"id": j["job_id"], "lease": j["lease_id"]},
                )

    async def process(self, j):
        identity = VerifiedIdentity(**j["actor_identity"])
        identifier, kind, payload = j["document_id"], j["kind"], j["payload"]
        async with self.sessions() as s:
            await admin(s, identity)
            d, old = await document(s, identifier), await latest(s, identifier)
        chunks, vectors = [], []
        profile = self.service.embedder.profile
        if kind in {"publish", "reindex"}:
            if kind == "publish":
                stamp = datetime.now(UTC)
                candidate = PgVersion(
                    identifier,
                    UUID(payload["version_id"]),
                    j["expected_revision"] + 1,
                    UUID(d["subject_id"]),
                    payload["title"],
                    payload["content_md"],
                    sha256(payload["content_md"]),
                    payload["metadata"],
                    stamp,
                    stamp,
                    "published",
                )
            else:
                require(old is not None, ErrorCode.VERSION_CONFLICT)
                candidate = version(d, old)
            chunks = prepare_chunks(candidate, profile)
            await self.stage(j, "embedding")
            # Shared model mutex with retrieval; no DB transaction is open here.
            vectors = await self.service.cpu(
                self.service.embedder.documents, [c.embedding_input for c in chunks]
            )
        await self.stage(j, "publishing")
        async with self.sessions() as s, s.begin():
            await s.execute(text("SET LOCAL lock_timeout='5s'"))
            current_job = await row(
                s, "SELECT * FROM core.knowledge_jobs WHERE job_id=:id FOR UPDATE", id=j["job_id"]
            )
            require(
                current_job["lease_id"] == j["lease_id"] and current_job["status"] == "processing",
                ErrorCode.LEASE_EXPIRED,
            )
            await admin(s, identity, lock=True)
            d = await document(s, identifier, lock=True)
            old = await latest(s, identifier)
            require(
                d["deleted_at"] is None
                and (old["version_no"] if old else 0) == j["expected_revision"],
                ErrorCode.VERSION_CONFLICT,
            )
            old_count = (
                await s.execute(
                    text("""
                SELECT count(*) FROM core.document_chunks WHERE version_id=:v
            """),
                    {"v": old["version_id"] if old else None},
                )
            ).scalar_one()
            if kind == "publish":
                await s.execute(
                    text("""
                    INSERT INTO core.document_versions(version_id,document_id,version_no,
                      title_snapshot,content_md,checksum_sha256,status,metadata,created_by_subject)
                    VALUES(:v,:id,:no,:title,:content,:hash,'published',CAST(:meta AS jsonb),:actor)
                """),
                    {
                        "v": candidate.version_id,
                        "id": identifier,
                        "no": candidate.version_no,
                        "title": candidate.title,
                        "content": candidate.content_md,
                        "hash": candidate.checksum,
                        "meta": json.dumps(candidate.metadata),
                        "actor": str(j["actor_user_id"]),
                    },
                )
                candidate = version(d, await latest(s, identifier))
                await s.execute(
                    text("UPDATE core.documents SET title=:title WHERE document_id=:id"),
                    {"title": candidate.title, "id": identifier},
                )
            if kind in {"publish", "reindex"}:
                await self.service.store.commit_generation(
                    s,
                    candidate,
                    chunks,
                    vectors,
                    profile,
                    PgScope(datetime.now(UTC), operator=True),
                )
            elif kind == "delete":
                require(
                    not (
                        await s.execute(
                            text("SELECT core.knowledge_is_referenced(:id)"), {"id": identifier}
                        )
                    ).scalar_one(),
                    ErrorCode.INVALID_STATE,
                )
                await s.execute(
                    text(
                        "UPDATE core.documents SET deleted_at=clock_timestamp() "
                        "WHERE document_id=:id"
                    ),
                    {"id": identifier},
                )
                await s.execute(
                    text(
                        "DELETE FROM core.document_chunks WHERE version_id IN "
                        "(SELECT version_id FROM core.document_versions "
                        "WHERE document_id=:id)"
                    ),
                    {"id": identifier},
                )
                await s.execute(
                    text("DELETE FROM core.document_versions WHERE document_id=:id"),
                    {"id": identifier},
                )
                await s.execute(
                    text("DELETE FROM core.documents WHERE document_id=:id"), {"id": identifier}
                )
                await s.execute(
                    text(
                        "UPDATE core.knowledge_jobs SET payload='{}'::jsonb WHERE document_id=:id"
                    ),
                    {"id": identifier},
                )
            else:
                await s.execute(
                    text(
                        "UPDATE core.documents SET deleted_at=clock_timestamp() "
                        "WHERE document_id=:id"
                    ),
                    {"id": identifier},
                )
            result = {
                "document_id": str(identifier),
                "old_chunk_count": old_count,
                "new_chunk_count": len(chunks),
                "revision": candidate.version_no if chunks else j["expected_revision"],
            }
            await s.execute(
                text("""
                UPDATE core.knowledge_jobs
                SET status='ready',stage='complete',result=CAST(:r AS jsonb),
                  payload=payload-'content_md',error_code=NULL,lease_until=NULL,
                  updated_at=clock_timestamp() WHERE job_id=:id
            """),
                {"id": j["job_id"], "r": json.dumps(result)},
            )
        log.info(
            "knowledge_manager document_id=%s job_id=%s stage=complete", identifier, j["job_id"]
        )

    async def run_once(self):
        # One model workload per process; DB leases also fence multiple workers.
        if self.service._gate.locked():
            return False
        async with self.service._gate:
            j = await self.claim()
            if not j:
                return False
            heartbeat = asyncio.create_task(self.heartbeat(j))
            try:
                async with asyncio.timeout(900):
                    await self.process(j)
            except asyncio.CancelledError:
                # Durable processing lease expires; the next process reclaims the same job.
                raise
            except Exception as exc:
                code = (
                    exc.code.value
                    if isinstance(exc, CoreError)
                    else ("MODEL_UNAVAILABLE" if isinstance(exc, KnowledgeError) else "SYNC_FAILED")
                )
                async with self.sessions() as s, s.begin():
                    await s.execute(
                        text("""
                        UPDATE core.knowledge_jobs
                        SET status='failed',stage='failed',error_code=:code,
                          lease_until=NULL,updated_at=clock_timestamp()
                        WHERE job_id=:id AND lease_id=:lease AND status='processing'
                    """),
                        {"id": j["job_id"], "lease": j["lease_id"], "code": code},
                    )
                log.warning("knowledge_manager job_id=%s stage=failed code=%s", j["job_id"], code)
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
            return True

    async def run(self):
        while True:
            try:
                worked = await self.run_once()
            except DBAPIError:
                log.warning("knowledge_manager stage=database_unavailable")
                worked = False
            await asyncio.sleep(0.1 if worked else 2)
