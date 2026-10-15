# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Source bindings and contract-shaped output for the local RAG pilot."""

from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from pydantic.json_schema import SkipJsonSchema


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentSpec(Record):
    document_id: UUID
    path: str
    supplier_id: UUID
    supplier_code: str
    supplier_name: str
    reviewer_user_id: UUID
    reviewer_name: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    known_from: AwareDatetime
    report_asof: AwareDatetime


class Catalog(Record):
    format_version: str = "flowbridge-local-knowledge-v1"
    dataset_id: str
    documents: list[DocumentSpec]


class DocumentReference(Record):
    document_id: str
    document_version: str
    chunk_id: str
    source_uri: str
    checksum: str
    supplier_id: UUID


class ChunkRecord(Record):
    reference: DocumentReference
    heading_path: list[str]
    reviewer_user_id: UUID
    reviewer_name: str
    start_char: int
    end_char: int
    start_line: int
    end_line: int
    text_sha256: str
    tokens: list[str]


class IndexSnapshot(Record):
    schema_version: str = "rag-index-v1"
    index_version: str
    collection: str
    embedding_profile: str
    documents: list[DocumentSpec]
    chunks: list[ChunkRecord]


class KnowledgeChunk(Record):
    reference: DocumentReference
    text: Annotated[str, Field(max_length=6000)]
    relevance_score: Annotated[float, Field(ge=0, le=1)]


class RetrievalRequest(Record):
    query: Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]
    supplier_id: UUID | SkipJsonSchema[None] = None
    top_k: Annotated[int, Field(ge=1, le=20, strict=True)] = 5

    @field_validator("supplier_id")
    @classmethod
    def supplied_supplier_must_be_uuid(cls, value: UUID | None) -> UUID:
        # Omission is allowed; explicit null is not a UUID in the shared API contract.
        if value is None:
            raise ValueError("supplier_id must be a UUID when supplied")
        return value


class RetrievalResponse(Record):
    query: str
    chunks: list[KnowledgeChunk]
    index_version: str
    warnings: list[str]


class RetrievalScope(Record):
    # Required explicit allowlist. A backend must derive it from trusted Core permissions.
    # The CLI is a trusted local operator, NOT an authentication implementation.
    allowed_document_ids: frozenset[UUID]
    supplier_id: UUID | None = None
    as_of: AwareDatetime | None = None


class KnowledgeError(RuntimeError):
    """Actionable local error; never turn a broken backend into an empty success."""
