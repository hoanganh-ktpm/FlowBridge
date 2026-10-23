import re
from typing import Protocol

from app.core.applications.manifest import fingerprint
from app.core.data_sources.discovery import SourceSnapshot, canonical_type
from app.core.hasura_provisioner.schemas import TableRef
from app.platform.http.errors import CoreError, ErrorCode

from .schemas import DataContractDocument


class PublicationEvidence(Protocol):
    async def require_uniqueness(
        self, product_key: str, columns: tuple[str, ...], snapshot_hash: str
    ) -> None: ...


def supported_pattern(pattern: str) -> bool:
    return bool(
        re.fullmatch(
            r"\^[A-Za-z0-9_ .:/-]*\[[A-Za-z0-9_ .:/\\^-]+\]"
            r"(?:[+*?]|\{[0-9]{1,6}(?:,[0-9]{0,6})?\})?[A-Za-z0-9_ .:/-]*\$",
            pattern,
        )
    )


def validate_physical_contract(
    product: dict, document: DataContractDocument, snapshot: SourceSnapshot
) -> bool:
    physical = snapshot.find(TableRef(schema=product["schema_name"], name=product["object_name"]))
    if (
        physical is None
        or physical.kind.upper() != product["object_type"]
        or document.classification != product["classification"]
    ):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    columns = {column.name: column for column in physical.columns}
    supported = {
        "text": {"string"},
        "uuid": {"string"},
        "character": {"string"},
        "integer": {"integer"},
        "smallint": {"integer"},
        "boolean": {"boolean"},
        "real": {"number"},
        "double precision": {"number"},
        "date": {"date"},
        "timestamptz": {"date-time"},
    }
    for field in document.fields:
        if (
            field.constraints
            and field.constraints.pattern is not None
            and not supported_pattern(field.constraints.pattern)
        ):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        column = columns.get(field.column)
        if (
            column is None
            or column.pg_type.lower() != field.physical_type.lower()
            or column.nullable != field.nullable
            or field.wire_type not in supported.get(canonical_type(column.pg_type), set())
            or field.wire_schema is not None
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
    keys = {field.column for field in document.fields if field.name in document.business_key}
    return any(
        key and set(key) <= keys for key in (physical.primary_key, *physical.unique_constraints)
    )


async def require_physical_contract(product, document, snapshot, evidence=None) -> None:
    if not validate_physical_contract(product, document, snapshot):
        if evidence is None:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        await evidence.require_uniqueness(
            product["product_key"],
            tuple(field.column for field in document.fields if field.name in document.business_key),
            fingerprint(snapshot.model_dump(mode="json", by_alias=True)),
        )
    if document.relationships:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
