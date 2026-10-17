# SPDX-License-Identifier: GPL-3.0-or-later
"""Short async transactions; embedding never holds a database transaction open."""

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import KnowledgeError
from .postgres_domain import (
    PgChunk,
    PgScope,
    PgVersion,
    latest_versions,
    validate_stored_chunks,
    vector_literal,
)

SOURCE_SQL = """
SELECT d.document_id, v.version_id, v.version_no, d.subject_id AS supplier_id,
       v.title_snapshot AS title, v.content_md, v.checksum_sha256 AS checksum,
       v.metadata, v.published_at, v.known_from, v.status, v.withdrawn_at
FROM core.documents d JOIN core.document_versions v USING(document_id)
WHERE d.deleted_at IS NULL AND d.document_kind='supplier_review' AND d.subject_type='supplier'
  AND v.status <> 'draft'
  AND (:operator OR d.scope_type='global'
       OR (d.scope_type='department' AND d.department_id=CAST(:department AS uuid)))
  AND (CAST(:supplier AS text) IS NULL OR d.subject_id=CAST(:supplier AS text))
"""


def source_row(row: dict) -> PgVersion:
    data = dict(row)
    data["supplier_id"] = UUID(data["supplier_id"])
    data["checksum"] = data["checksum"].strip()
    return PgVersion(**data)


def scope_parameters(scope: PgScope) -> dict:
    return {
        "operator": scope.operator,
        "department": scope.department_id,
        "supplier": str(scope.supplier_id) if scope.supplier_id else None,
    }


class PgKnowledgeStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def verify_schema(self) -> None:
        async with self.sessions() as session:
            ok = (
                await session.execute(
                    text("""
                SELECT
                  (SELECT count(*)=3 AND bool_and(c.relrowsecurity)
                   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='core' AND c.relname IN
                     ('documents','document_versions','document_chunks'))
                  AND (SELECT count(*)=37 FROM information_schema.columns
                    WHERE table_schema='core' AND table_name IN
                      ('documents','document_versions','document_chunks'))
                  AND (SELECT format_type(atttypid,atttypmod) LIKE '%vector(1024)'
                    FROM pg_attribute WHERE attrelid='core.document_chunks'::regclass
                      AND attname='embedding')
                  AND (SELECT count(*)=2 FROM pg_trigger WHERE NOT tgisinternal
                    AND tgenabled<>'D' AND (tgrelid='core.document_versions'::regclass
                      AND tgname='trg_guard_published_document_versions'
                      OR tgrelid='core.document_chunks'::regclass
                      AND tgname='trg_guard_document_chunk_source'))
            """)
                )
            ).scalar_one()
            if ok is not True:
                raise KnowledgeError("SCHEMA_NOT_READY: apply Core migration 0003")

    async def resolve_reference(self, version: PgVersion, reference: dict) -> dict:
        from .sources import sha256

        version.spec()
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        text("""
                SELECT * FROM core.document_chunks WHERE chunk_id=CAST(:id AS uuid)
                  AND version_id=:version
            """),
                        {"id": reference["chunk_id"], "version": version.version_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise KnowledgeError("CHUNK_UNAVAILABLE: reference not retained")
        start, end = row["source_start_byte"], row["source_end_byte"]
        raw = version.content_md.encode("utf-8")
        if not 0 <= start < end <= len(raw):
            raise KnowledgeError("SOURCE_CHANGED: invalid byte offsets")
        try:
            quote = raw[start:end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise KnowledgeError("SOURCE_CHANGED: offsets split a UTF-8 character") from exc
        expected = {
            "document_id": str(version.document_id),
            "document_version": str(version.version_id),
            "chunk_id": str(row["chunk_id"]),
            "checksum": version.checksum,
            "supplier_id": str(version.supplier_id),
            "source_uri": f"core-document://{version.document_id}/{version.version_id}"
            f"#bytes={start}:{end}",
        }
        if (
            reference != expected
            or quote != row["content"]
            or row["source_checksum"] != version.checksum
            or row["chunk_checksum"] != sha256(quote)
        ):
            raise KnowledgeError("SOURCE_CHANGED: quotation checksum or reference mismatch")
        return {
            "reference": expected,
            "text": quote,
            "heading_path": row["heading_path"],
            "reviewer_user_id": version.metadata["reviewer_user_id"],
            "reviewer_name": version.metadata["reviewer_name_snapshot"],
        }

    async def versions(self, scope: PgScope, *, latest: bool = True) -> list[PgVersion]:
        async with self.sessions() as session:
            rows = (await session.execute(text(SOURCE_SQL), scope_parameters(scope))).mappings()
            versions = [source_row(row) for row in rows]
        return latest_versions(versions, scope.as_of) if latest else versions

    async def chunks(self, chunks: list[PgChunk], profile: str) -> list[dict]:
        if not chunks:
            return []
        async with self.sessions() as session:
            result = await session.execute(
                text("""
                SELECT chunk_id,version_id,chunk_no,content,heading_path,source_start_byte,
                  source_end_byte,source_checksum,chunk_checksum,index_generation
                FROM core.document_chunks
                WHERE version_id=ANY(CAST(:versions AS uuid[]))
                  AND index_generation=ANY(CAST(:generations AS text[]))
                  AND embedding_profile=:profile
                ORDER BY version_id,chunk_no
            """),
                {
                    "versions": list({c.version_id for c in chunks}),
                    "generations": list({c.generation for c in chunks}),
                    "profile": profile,
                },
            )
            return [dict(row) for row in result.mappings()]

    async def dense(self, chunks: list[PgChunk], profile: str, vector: list[float]) -> list[str]:
        if not chunks:
            return []
        async with self.sessions() as session:
            result = await session.execute(
                text("""
                SELECT chunk_id FROM core.document_chunks
                WHERE version_id=ANY(CAST(:versions AS uuid[]))
                  AND index_generation=ANY(CAST(:generations AS text[]))
                  AND embedding_profile=:profile
                ORDER BY embedding OPERATOR(extensions.<=>)
                  CAST(:embedding AS extensions.vector(1024)),chunk_id
                LIMIT 40
            """),
                {
                    "versions": list({c.version_id for c in chunks}),
                    "generations": list({c.generation for c in chunks}),
                    "profile": profile,
                    "embedding": vector_literal(vector),
                },
            )
            return [str(value) for value in result.scalars()]

    async def write_generation(
        self,
        version: PgVersion,
        chunks: list[PgChunk],
        vectors: list[list[float]],
        profile: str,
        scope: PgScope,
    ) -> None:
        if not chunks or len(chunks) != len(vectors):
            raise KnowledgeError("INCOMPLETE_GENERATION: embedding count mismatch")
        parameters = []
        for number, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            parameters.append(
                {
                    "id": UUID(chunk.record.reference.chunk_id),
                    "version": version.version_id,
                    "number": number,
                    "content": chunk.content,
                    "headings": chunk.record.heading_path,
                    "start": chunk.start_byte,
                    "end": chunk.end_byte,
                    "source": version.checksum,
                    "checksum": chunk.record.text_sha256,
                    "embedding": vector_literal(vector),
                    "profile": profile,
                    "generation": chunk.generation,
                }
            )
        async with self.sessions() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {
                    "key": f"knowledge:{version.version_id}:{chunks[0].generation}",
                },
            )
            current = (
                (
                    await session.execute(
                        text(SOURCE_SQL + " AND v.version_id=:version FOR SHARE OF d,v"),
                        {
                            **scope_parameters(scope),
                            "version": version.version_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None or source_row(current) != version or version.status != "published":
                raise KnowledgeError("SOURCE_CHANGED: source no longer eligible")
            # Atomic replacement of this deterministic generation only. Other generations
            # remain available for persisted references. A failed batch rolls back entirely.
            await session.execute(
                text("""
                DELETE FROM core.document_chunks WHERE version_id=:version
                  AND index_generation=:generation
            """),
                {"version": version.version_id, "generation": chunks[0].generation},
            )
            await session.execute(
                text("""
                INSERT INTO core.document_chunks(chunk_id,version_id,chunk_no,content,heading_path,
                  source_start_byte,source_end_byte,source_checksum,chunk_checksum,
                  embedding,embedding_profile,index_generation)
                VALUES(:id,:version,:number,:content,:headings,:start,:end,:source,:checksum,
                  CAST(:embedding AS extensions.vector(1024)),:profile,:generation)
            """),
                parameters,
            )
            rows = (
                await session.execute(
                    text("""
                SELECT * FROM core.document_chunks WHERE version_id=:version
                  AND index_generation=:generation ORDER BY chunk_no
            """),
                    {"version": version.version_id, "generation": chunks[0].generation},
                )
            ).mappings()
            if not validate_stored_chunks(chunks, [dict(row) for row in rows]):
                raise KnowledgeError("INCOMPLETE_GENERATION: verification failed")

    async def import_document(
        self, spec, content: str, actor: str, department: UUID | None
    ) -> UUID:
        # This is a trusted operator command. Reviewer identity is validated, not impersonated.
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        from .sources import sha256

        checksum = sha256(content)
        metadata = {
            "supplier_id": str(spec.supplier_id),
            "supplier_code": spec.supplier_code,
            "supplier_name": spec.supplier_name,
            "reviewer_user_id": str(spec.reviewer_user_id),
            "reviewer_name_snapshot": spec.reviewer_name,
            "report_asof": spec.report_asof.isoformat(),
            "reviewer_attribution": "synthetic_assignment_from_seed",
        }
        async with self.sessions() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {
                    "key": "knowledge-import:" + str(spec.supplier_id),
                },
            )
            valid = (
                await session.execute(
                    text("""
                SELECT EXISTS(SELECT 1 FROM public.suppliers WHERE supplier_id=:supplier)
                  AND EXISTS(SELECT 1 FROM public.users WHERE user_id=:reviewer AND status='active')
                  AND (CAST(:dept AS uuid) IS NULL OR EXISTS(SELECT 1 FROM public.departments
                    WHERE department_id=:dept AND status='active'))
            """),
                    {
                        "supplier": spec.supplier_id,
                        "reviewer": spec.reviewer_user_id,
                        "dept": department,
                    },
                )
            ).scalar_one()
            if not valid:
                raise KnowledgeError("INVALID_BINDING: supplier/reviewer/department must exist")
            document = (
                (
                    await session.execute(
                        text("""
                SELECT * FROM core.documents WHERE document_kind='supplier_review'
                  AND subject_type='supplier' AND subject_id=:supplier AND deleted_at IS NULL
                FOR UPDATE
            """),
                        {"supplier": str(spec.supplier_id)},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if document is None:
                document_id = (
                    await session.execute(
                        text("""
                    INSERT INTO core.documents(document_kind,subject_type,subject_id,title,
                      scope_type,department_id,owner_subject)
                    VALUES('supplier_review','supplier',:supplier,:title,:scope,:dept,:actor)
                    RETURNING document_id
                """),
                        {
                            "supplier": str(spec.supplier_id),
                            "title": spec.supplier_name,
                            "scope": "department" if department else "global",
                            "dept": department,
                            "actor": actor,
                        },
                    )
                ).scalar_one()
            else:
                if document["department_id"] != department:
                    raise KnowledgeError(
                        "SCOPE_CONFLICT: import cannot silently change document ACL"
                    )
                document_id = document["document_id"]
            old = (
                (
                    await session.execute(
                        text("""
                SELECT version_id,checksum_sha256,metadata,status FROM core.document_versions
                WHERE document_id=:id ORDER BY version_no DESC LIMIT 1
            """),
                        {"id": document_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if old and old["checksum_sha256"] == checksum and old["metadata"] == metadata:
                if old["status"] != "published":
                    raise KnowledgeError("SOURCE_WITHDRAWN: explicitly publish a corrected version")
                return old["version_id"]
            return (
                await session.execute(
                    text("""
                INSERT INTO core.document_versions(document_id,version_no,title_snapshot,
                  content_md,checksum_sha256,status,metadata,created_by_subject)
                SELECT :id,COALESCE(MAX(version_no),0)+1,:title,:content,:checksum,'published',
                  CAST(:metadata AS jsonb),:actor
                FROM core.document_versions WHERE document_id=:id RETURNING version_id
            """),
                    {
                        "id": document_id,
                        "title": spec.supplier_name,
                        "content": content,
                        "checksum": checksum,
                        "metadata": json.dumps(metadata),
                        "actor": actor,
                    },
                )
            ).scalar_one()
