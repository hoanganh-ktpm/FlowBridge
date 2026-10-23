from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, Field, field_validator

from app.core.hasura_provisioner.schemas import DTO


def public_identifier(value: str) -> str:
    if value.startswith("__"):
        raise ValueError("Reserved identifier")
    return value


Identifier = Annotated[
    str,
    Field(min_length=1, max_length=63, pattern=r"^[a-z_][a-z0-9_]*$"),
    AfterValidator(public_identifier),
]
LogicalKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,62}$")]
QualifiedKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,62}\.[a-z][a-z0-9_-]{0,62}$")]
Revision = Annotated[int, Field(ge=1, strict=True)]
Description = Annotated[str, Field(min_length=1, max_length=2000)]
Classification = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL"]


class RevisionCommand(DTO):
    expected_revision: Revision


class ReasonCommand(RevisionCommand):
    reason: str = Field(min_length=1, max_length=1000)


class RowScope(DTO):
    kind: Literal["ALL"]


class DataScope(DTO):
    action: Literal["SELECT"]
    columns: tuple[Identifier, ...] = Field(min_length=1, max_length=100)
    row_scope: RowScope

    @field_validator("columns")
    @classmethod
    def unique_columns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(item.startswith("__") for item in value):
            raise ValueError("Invalid projection")
        return value


class AccessRequestCreate(DTO):
    data_product_id: UUID
    contract_version_id: UUID
    consumer_department_id: UUID
    consumer_principal_id: None
    requested_scope_json: DataScope
    reason: Description


class ApproveRequest(RevisionCommand):
    scope_json: DataScope
    expires_at: datetime | None
    decision_note: Description | None = None

    @field_validator("expires_at", mode="before")
    @classmethod
    def utc_wire_format(cls, value):
        if isinstance(value, str) and not value.endswith("Z"):
            raise ValueError("UTC Z timestamp required")
        return value

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.utcoffset() is None or value.utcoffset().total_seconds()):
            raise ValueError("UTC timestamp required")
        return value


class RevokeGrant(DTO):
    expected_desired_revision: Revision
    reason: str = Field(min_length=1, max_length=1000)


RESOURCE_METADATA = {"id", "created_at", "updated_at", "created_by", "updated_by", "revision"}
PUBLIC_FIELDS = {
    "application": RESOURCE_METADATA
    | {"department_id", "app_key", "name", "description", "business_api_base_url", "status"},
    "source": RESOURCE_METADATA
    | {
        "application_id",
        "source_key",
        "db_type",
        "hasura_source_name",
        "status",
        "last_checked_at",
    },
    "product": RESOURCE_METADATA
    | {
        "data_source_id",
        "product_key",
        "name",
        "description",
        "schema_name",
        "object_name",
        "object_type",
        "classification",
        "owner_department_id",
        "active_contract_id",
        "status",
    },
    "contract": RESOURCE_METADATA
    | {"data_product_id", "version", "contract_json", "status", "published_at", "published_by"},
    "request": RESOURCE_METADATA
    | {
        "data_product_id",
        "contract_version_id",
        "consumer_department_id",
        "consumer_principal_id",
        "requested_scope_json",
        "reason",
        "requested_by",
        "requested_at",
        "status",
        "decision_note",
        "decided_by",
        "decided_at",
    },
    "grant": {
        "id",
        "data_product_id",
        "contract_version_id",
        "access_request_id",
        "consumer_department_id",
        "consumer_principal_id",
        "scope_json",
        "desired_revision",
        "applied_revision",
        "status",
        "expires_at",
        "granted_by",
        "granted_at",
        "revoked_by",
        "revoked_at",
        "updated_at",
    },
    "job": {
        "id",
        "job_type",
        "data_source_id",
        "data_product_id",
        "access_grant_id",
        "target_revision",
        "status",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "last_error_code",
        "created_at",
        "updated_at",
    },
}


def public_projection(kind, resource):
    from app.platform.http.errors import CoreError, ErrorCode

    fields = PUBLIC_FIELDS[kind]
    if not fields <= resource.public.keys():
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    return {name: resource.public[name] for name in fields}
