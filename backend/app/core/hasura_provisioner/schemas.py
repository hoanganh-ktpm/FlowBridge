from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")]
EnvironmentName = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class TableRef(DTO):
    schema_name: Identifier = Field(serialization_alias="schema", validation_alias="schema")
    name: Identifier

    def payload(self) -> dict[str, str]:
        return self.model_dump(by_alias=True)


class MetadataSnapshot(DTO):
    resource_version: int = Field(ge=1, strict=True)
    metadata: dict[str, Any] = Field(repr=False)


class SourceSummary(DTO):
    name: str
    kind: str


class ConsistencyReport(DTO):
    is_consistent: bool = Field(strict=True)
    inconsistent_count: int = Field(ge=0)


class PostgresConnection(DTO):
    environment_name: EnvironmentName
    database_url: SecretStr = Field(repr=False)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            parsed = urlsplit(value.get_secret_value())
            options = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
            valid = (
                parsed.scheme in {"postgres", "postgresql"}
                and parsed.hostname
                and parsed.username
                and parsed.password
                and parsed.path not in {"", "/"}
                and not parsed.fragment
                and (parsed.port is None or 1 <= parsed.port <= 65535)
                and options.get("sslmode") in [["require"], ["verify-ca"], ["verify-full"]]
                and set(options) <= {"sslmode", "connect_timeout", "application_name"}
                and all(len(values) == 1 for values in options.values())
                and (
                    "connect_timeout" not in options
                    or 1 <= int(options["connect_timeout"][0]) <= 120
                )
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Expected a PostgreSQL URI with credentials and TLS") from None
        return value

    @property
    def hostname(self) -> str:
        return urlsplit(self.database_url.get_secret_value()).hostname or ""


class SourceRecord(DTO):
    id: UUID
    name: Identifier = Field(validation_alias=AliasChoices("name", "hasura_source_name"))
    secret_ref: str = Field(min_length=1, max_length=200)
    db_type: Literal["POSTGRESQL"] = "POSTGRESQL"
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(min_length=1)
    db_user: str = Field(min_length=1)
    ssl_mode: Literal["require", "verify-ca", "verify-full"] = "verify-full"
    status: Literal["DRAFT", "VALIDATING", "CONNECTED", "FAILED", "DISABLED"] = "DRAFT"
    approved: bool = False
    revision: int = Field(ge=1)

    def matches_connection(self, connection: PostgresConnection) -> bool:
        parsed = urlsplit(connection.database_url.get_secret_value())
        return (
            parsed.hostname == self.host.lower()
            and (parsed.port or 5432) == self.port
            and unquote(parsed.path[1:]) == self.database_name
            and unquote(parsed.username or "") == self.db_user
            and parse_qs(parsed.query).get("sslmode") == [self.ssl_mode]
        )


class ProductRecord(DTO):
    id: UUID
    source_id: UUID = Field(validation_alias=AliasChoices("source_id", "data_source_id"))
    table: TableRef
    contract_id: UUID = Field(validation_alias=AliasChoices("contract_id", "active_contract_id"))
    status: Literal["DRAFT", "READY", "PROVISIONING", "ACTIVE", "ERROR", "DISABLED"] = "DRAFT"
    approved: bool = False
    revision: int = Field(ge=1)


class ValidatedContract(DTO):
    product_id: UUID
    contract_id: UUID
    status: Literal["DRAFT", "PUBLISHED", "RETIRED"] = "DRAFT"
    columns: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("columns")
    @classmethod
    def distinct_columns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Duplicate columns")
        return value


class AllRows(DTO):
    kind: Literal["ALL"]


class SelectScope(DTO):
    action: Literal["SELECT"]
    columns: tuple[Identifier, ...] = Field(min_length=1)
    row_scope: AllRows


class SelectGrant(DTO):
    id: UUID
    product_id: UUID = Field(validation_alias=AliasChoices("product_id", "data_product_id"))
    contract_id: UUID = Field(validation_alias=AliasChoices("contract_id", "contract_version_id"))
    department_id: UUID = Field(
        validation_alias=AliasChoices("department_id", "consumer_department_id")
    )
    consumer_principal_id: None = None
    columns: tuple[Identifier, ...] = Field(min_length=1)
    row_scope: Literal["ALL"]
    approved: bool = False
    status: Literal["PENDING_APPLY", "ACTIVE", "REVOKING", "REVOKED", "EXPIRED", "ERROR"]
    expires_at: datetime | None = None
    desired_revision: int = Field(
        ge=1, validation_alias=AliasChoices("desired_revision", "revision")
    )
    applied_revision: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def expand_scope(cls, value: Any) -> Any:
        if isinstance(value, dict) and "scope_json" in value:
            value = dict(value)
            if "columns" in value or "row_scope" in value:
                raise ValueError("Ambiguous scope projection")
            scope = SelectScope.model_validate(value.pop("scope_json"))
            value["columns"] = scope.columns
            value["row_scope"] = scope.row_scope.kind
        return value

    @model_validator(mode="after")
    def validate_revisions(self) -> "SelectGrant":
        if self.applied_revision > self.desired_revision or (
            self.status == "ACTIVE" and self.applied_revision != self.desired_revision
        ):
            raise ValueError("Invalid desired/applied revision state")
        return self

    @property
    def revision(self) -> int:
        return self.desired_revision

    @property
    def enforcement_role(self) -> str:
        return department_role(self.department_id)

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value


class PermissionTarget(DTO):
    product_id: UUID
    department_id: UUID


JobType = Literal[
    "CONNECT_SOURCE", "PUBLISH_PRODUCT", "APPLY_GRANT", "REVOKE_GRANT", "DISABLE_PRODUCT"
]


class PublicationJob(DTO):
    id: UUID
    job_type: JobType
    data_source_id: UUID | None = None
    data_product_id: UUID | None = None
    access_grant_id: UUID | None = None
    target_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1)
    status: Literal["QUEUED", "RUNNING", "RETRY", "SUCCEEDED", "FAILED"] = "QUEUED"
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    next_attempt_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    locked_by: str | None = None
    lease_expires_at: datetime | None = None

    @field_validator("next_attempt_at", "lease_expires_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value

    @model_validator(mode="after")
    def validate_target_and_lease(self) -> "PublicationJob":
        target = (
            self.data_source_id
            if self.job_type == "CONNECT_SOURCE"
            else self.data_product_id
            if self.job_type in {"PUBLISH_PRODUCT", "DISABLE_PRODUCT"}
            else self.access_grant_id
        )
        if (
            target is None
            or sum(
                value is not None
                for value in (self.data_source_id, self.data_product_id, self.access_grant_id)
            )
            != 1
        ):
            raise ValueError("Job requires exactly one matching target")
        if self.attempt_count > self.max_attempts:
            raise ValueError("Attempt limit exceeded")
        if self.status == "RUNNING" and (not self.locked_by or self.lease_expires_at is None):
            raise ValueError("Running job requires a lease")
        return self


class SelectPermission(DTO):
    columns: tuple[Identifier, ...] = Field(min_length=1)
    limit: int = Field(default=1000, ge=1, le=10000)

    def payload(self) -> dict[str, Any]:
        return {
            "columns": sorted(set(self.columns)),
            "filter": {},
            "limit": self.limit,
            "allow_aggregations": False,
            "query_root_fields": ["select"],
            "subscription_root_fields": [],
        }


def department_role(department_id: UUID) -> str:
    return f"dxos_department_{department_id.hex}"
