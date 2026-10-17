# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable PostgreSQL source versions and byte-accurate derived chunks."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from .chunking import CHUNKER_VERSION, chunk_document, embedding_text
from .models import ChunkRecord, DocumentReference, DocumentSpec, KnowledgeError
from .sources import sha256


@dataclass(frozen=True)
class PgScope:
    as_of: datetime
    department_id: UUID | None = None
    supplier_id: UUID | None = None
    operator: bool = False


@dataclass(frozen=True)
class PgVersion:
    document_id: UUID
    version_id: UUID
    version_no: int
    supplier_id: UUID
    title: str
    content_md: str
    checksum: str
    metadata: dict
    published_at: datetime
    known_from: datetime
    status: str
    withdrawn_at: datetime | None = None

    def spec(self) -> DocumentSpec:
        if sha256(self.content_md) != self.checksum:
            raise KnowledgeError("SOURCE_CHANGED: source checksum does not match")
        try:
            if UUID(self.metadata["supplier_id"]) != self.supplier_id:
                raise ValueError("Supplier binding mismatch")
            return DocumentSpec(
                document_id=self.document_id,
                path=f"postgres://core.document_versions/{self.version_id}",
                supplier_id=self.supplier_id,
                supplier_code=str(self.metadata.get("supplier_code", self.supplier_id)),
                supplier_name=str(self.metadata.get("supplier_name", self.title)),
                reviewer_user_id=UUID(self.metadata["reviewer_user_id"]),
                reviewer_name=self.metadata["reviewer_name_snapshot"],
                sha256=self.checksum,
                known_from=self.known_from,
                report_asof=self.metadata["report_asof"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise KnowledgeError(
                "INVALID_SOURCE_METADATA: validate supplier/reviewer metadata"
            ) from exc


@dataclass(frozen=True)
class PgChunk:
    record: ChunkRecord
    version_id: UUID
    generation: str
    start_byte: int
    end_byte: int
    content: str
    embedding_input: str


def generation_for(version: PgVersion, profile: str) -> str:
    payload = ["pg-rag-v1", str(version.version_id), version.checksum, CHUNKER_VERSION, profile]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def latest_versions(rows: list[PgVersion], as_of: datetime) -> list[PgVersion]:
    if as_of.tzinfo is None:
        raise KnowledgeError("INVALID_CUTOFF: timezone is required")
    latest: dict[UUID, PgVersion] = {}
    for row in rows:
        if row.status == "draft" or row.known_from > as_of or row.published_at > as_of:
            continue
        old = latest.get(row.document_id)
        if old is None or (row.known_from, row.version_no) > (old.known_from, old.version_no):
            latest[row.document_id] = row
    # Current withdrawal is a safety veto even for historical replay. Never silently
    # fall back to the previous version of a withdrawn latest-known source.
    return sorted(
        (row for row in latest.values() if row.status == "published"),
        key=lambda row: str(row.document_id),
    )


def prepare_chunks(version: PgVersion, profile: str) -> list[PgChunk]:
    spec = version.spec()
    generation = generation_for(version, profile)
    raw = version.content_md.encode("utf-8")
    result = []
    for chunk in chunk_document("flowbridge-core", spec, version.content_md):
        start = len(version.content_md[: chunk.start_char].encode("utf-8"))
        end = len(version.content_md[: chunk.end_char].encode("utf-8"))
        quote = raw[start:end].decode("utf-8")
        identity = f"{version.version_id}/{generation}/{start}:{end}/{sha256(quote)}"
        reference = DocumentReference(
            document_id=str(version.document_id),
            document_version=str(version.version_id),
            chunk_id=str(uuid5(NAMESPACE_URL, identity)),
            source_uri=(
                f"core-document://{version.document_id}/{version.version_id}#bytes={start}:{end}"
            ),
            checksum=version.checksum,
            supplier_id=version.supplier_id,
        )
        record = chunk.model_copy(update={"reference": reference})
        result.append(
            PgChunk(
                record,
                version.version_id,
                generation,
                start,
                end,
                quote,
                embedding_text(spec, record, quote),
            )
        )
    if not result:
        raise KnowledgeError("EMPTY_SOURCE: published Markdown must contain text")
    return result


def vector_literal(vector: list[float]) -> str:
    if len(vector) != 1024 or any(not math.isfinite(v) for v in vector):
        raise KnowledgeError("INVALID_VECTOR: expected 1024 finite dimensions")
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isclose(norm, 1.0, abs_tol=1e-4):
        raise KnowledgeError("INVALID_VECTOR: expected a unit-normalized vector")
    return "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"


def validate_stored_chunks(expected: list[PgChunk], rows: list[dict]) -> bool:
    if len(expected) != len(rows):
        return False
    stored = {str(row["chunk_id"]): row for row in rows}
    for number, chunk in enumerate(expected):
        row = stored.get(chunk.record.reference.chunk_id)
        if row is None or any(
            (
                str(row["version_id"]) != str(chunk.version_id),
                row["index_generation"] != chunk.generation,
                row["chunk_no"] != number,
                row["source_checksum"] != chunk.record.reference.checksum,
                row["chunk_checksum"] != chunk.record.text_sha256,
                row["source_start_byte"] != chunk.start_byte,
                row["source_end_byte"] != chunk.end_byte,
                row["content"] != chunk.content,
                list(row["heading_path"] or []) != chunk.record.heading_path,
            )
        ):
            return False
    return True
