import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.core.hasura_provisioner.schemas import DTO, Identifier, TableRef

Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,62}$")]
Reference = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,62}(\.[a-z][a-z0-9_-]{0,62})?$")]
SecretReference = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_./-]{0,199}$")]
DataType = Literal[
    "text",
    "integer",
    "bigint",
    "smallint",
    "numeric",
    "double precision",
    "real",
    "boolean",
    "uuid",
    "date",
    "timestamp",
    "timestamptz",
    "json",
    "jsonb",
]


class ManifestError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class Application(DTO):
    key: Key
    name: str = Field(min_length=1, max_length=200)
    department_key: Key
    description: str | None = Field(default=None, max_length=2000)


class Source(DTO):
    key: Key
    kind: Literal["postgres"] = "postgres"
    host: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
    port: int = Field(default=5432, ge=1, le=65535, strict=True)
    database: str = Field(min_length=1, max_length=63, pattern=r"^[A-Za-z0-9_-]+$")
    username: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    ssl_mode: Literal["require", "verify-ca", "verify-full"] = "verify-full"
    secret_ref: SecretReference


class PhysicalObject(TableRef):
    kind: Literal["table", "view"]


class PublishedField(DTO):
    column: Identifier
    alias: Identifier | None = None
    expected_type: DataType | None = None
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("alias")
    @classmethod
    def reserved_alias(cls, value: str | None) -> str | None:
        if value is not None and value.startswith("__"):
            raise ValueError("Reserved public name")
        return value


class JoinColumn(DTO):
    local: Identifier = Field(validation_alias="from", serialization_alias="from")
    remote: Identifier = Field(validation_alias="to", serialization_alias="to")


class Relationship(DTO):
    name: Identifier
    type: Literal["object", "array"]
    target_product: Reference
    strategy: Literal["fk", "manual", "remote"]
    mapping: tuple[JoinColumn, ...] = Field(min_length=1)
    uniqueness_ref: SecretReference | None = None
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_mapping(self) -> "Relationship":
        if self.name.startswith("__"):
            raise ValueError("Reserved relationship name")
        if len({pair.local for pair in self.mapping}) != len(self.mapping) or len(
            {pair.remote for pair in self.mapping}
        ) != len(self.mapping):
            raise ValueError("Join mapping must be one-to-one")
        return self


class Product(DTO):
    key: Key
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    source_key: Key
    object: PhysicalObject
    fields: tuple[PublishedField, ...] = Field(min_length=1)
    relationships: tuple[Relationship, ...] = ()
    contract_version: str = Field(
        default="1.0.0", pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
    )
    requested_state: Literal["DRAFT", "PUBLISH_REQUESTED"] = "DRAFT"

    @model_validator(mode="after")
    def unique_names(self) -> "Product":
        columns = [field.column for field in self.fields]
        names = [field.alias or field.column for field in self.fields]
        names += [relationship.name for relationship in self.relationships]
        if (
            len(set(columns)) != len(columns)
            or len(set(names)) != len(names)
            or any(name.startswith("__") for name in names)
        ):
            raise ValueError("Duplicate column/public field/relationship name")
        return self


class ApplicationManifest(DTO):
    manifest_version: Literal["1.0"]
    application: Application
    sources: tuple[Source, ...] = Field(min_length=1)
    data_products: tuple[Product, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_references(self) -> "ApplicationManifest":
        source_keys = [source.key for source in self.sources]
        product_keys = [product.key for product in self.data_products]
        if len(set(source_keys)) != len(source_keys) or len(set(product_keys)) != len(product_keys):
            raise ValueError("Duplicate source/product key")
        physical = [(p.source_key, p.object.schema_name, p.object.name) for p in self.data_products]
        if len(set(physical)) != len(physical):
            raise ValueError("A physical object must have one product")
        for product in self.data_products:
            if product.source_key not in source_keys:
                raise ValueError("Unknown source reference")
            for relationship in product.relationships:
                target = relationship.target_product
                local = target.removeprefix(self.application.key + ".")
                if "." not in local and local not in product_keys:
                    raise ValueError("Unknown local product reference")
        return self


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("Duplicate JSON object key")
        result[key] = value
    return result


def parse_manifest(content: str) -> ApplicationManifest:
    if len(content.encode("utf-8")) > 2_000_000:
        raise ManifestError("Manifest exceeds 2 MB")
    try:
        value = json.loads(content, object_pairs_hook=_unique_json_object)
        if isinstance(value, dict) and value.get("manifest_version") != "1.0":
            raise ManifestError("Unsupported manifest_version; expected 1.0")
        return ApplicationManifest.model_validate(value)
    except ValidationError as error:
        locations = [".".join(map(str, item["loc"])) or "manifest" for item in error.errors()]
        raise ManifestError("Invalid manifest at: " + ", ".join(locations)) from None
    except (json.JSONDecodeError, RecursionError):
        raise ManifestError("Invalid JSON manifest") from None


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def source_name(application_key: str, source_key: str) -> str:
    readable = f"{application_key}_{source_key}".replace("-", "_")[:48]
    return f"{readable}_{fingerprint([application_key, source_key])[:10]}"


def manifest_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://flowbridge.example.invalid/schemas/application-manifest/1.0",
        **ApplicationManifest.model_json_schema(by_alias=True),
    }
