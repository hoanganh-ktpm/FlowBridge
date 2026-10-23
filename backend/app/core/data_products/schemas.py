import re
from typing import Annotated, Any, Literal
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import Field, field_validator, model_validator

from app.core.applications.manifest import JoinColumn
from app.core.applications.manifest import Relationship as ManifestRelationship
from app.core.governance.schemas import (
    Classification,
    Description,
    Identifier,
    QualifiedKey,
    RevisionCommand,
)
from app.core.hasura_provisioner.schemas import DTO

SEMVER = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"


class FieldConstraints(DTO):
    minimum: (
        Annotated[int, Field(strict=True)]
        | Annotated[float, Field(strict=True, allow_inf_nan=False)]
        | None
    ) = None
    maximum: (
        Annotated[int, Field(strict=True)]
        | Annotated[float, Field(strict=True, allow_inf_nan=False)]
        | None
    ) = None
    min_length: int | None = Field(default=None, ge=0, le=1000000, strict=True)
    max_length: int | None = Field(default=None, ge=0, le=1000000, strict=True)
    pattern: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("pattern")
    @classmethod
    def valid_pattern(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error:
                raise ValueError("Invalid pattern") from None
        return value

    @model_validator(mode="before")
    @classmethod
    def no_null_constraints(cls, value):
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("Omit absent constraints instead of null")
        return value


class ContractField(DTO):
    column: Identifier
    name: Identifier
    physical_type: str = Field(min_length=1, max_length=100)
    wire_type: Literal[
        "string", "integer", "number", "boolean", "date", "date-time", "object", "array"
    ]
    nullable: bool = Field(strict=True)
    description: Description | None
    constraints: FieldConstraints | None = None
    wire_schema: dict[str, Any] | None = Field(default=None, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def no_null_options(cls, value):
        if isinstance(value, dict) and any(
            name in value and value[name] is None for name in ("constraints", "wire_schema")
        ):
            raise ValueError("Omit absent field options instead of null")
        return value

    @field_validator("wire_schema")
    @classmethod
    def valid_wire_schema(cls, value):
        if value is not None:
            try:
                Draft202012Validator.check_schema(value)
            except SchemaError:
                raise ValueError("Invalid wire schema") from None
        return value

    @model_validator(mode="after")
    def compatible_constraints(self) -> "ContractField":
        constraints = self.constraints
        if constraints:
            bounds = constraints.model_dump(exclude_none=True)
            if {"minimum", "maximum"} & bounds.keys() and self.wire_type not in {
                "integer",
                "number",
            }:
                raise ValueError("Numeric constraints require a numeric wire type")
            if {"min_length", "max_length", "pattern"} & bounds.keys() and self.wire_type not in {
                "string",
                "date",
                "date-time",
            }:
                raise ValueError("String constraints require a string wire type")
            for lower, upper in [
                (constraints.minimum, constraints.maximum),
                (constraints.min_length, constraints.max_length),
            ]:
                if lower is not None and upper is not None and lower > upper:
                    raise ValueError("Contradictory bounds")
        return self


class Relationship(ManifestRelationship):
    target_product: QualifiedKey
    mapping: tuple[JoinColumn, ...] = Field(min_length=1, max_length=20)
    description: Description | None = None


class DataContractDocument(DTO):
    document_version: Literal["1.0"]
    grain: str = Field(min_length=1, max_length=500)
    business_key: tuple[Identifier, ...] = Field(min_length=1, max_length=20)
    classification: Classification
    description: Description | None
    fields: tuple[ContractField, ...] = Field(min_length=1, max_length=100)
    relationships: tuple[Relationship, ...] = Field(max_length=50)

    @model_validator(mode="after")
    def unambiguous_mapping(self) -> "DataContractDocument":
        physical = [item.column for item in self.fields]
        public = [item.name for item in self.fields]
        names = public + [item.name for item in self.relationships]
        if (
            len(set(physical)) != len(physical)
            or len(set(names)) != len(names)
            or any(item.startswith("__") for item in physical + names)
            or len(set(self.business_key)) != len(self.business_key)
            or not set(self.business_key) <= set(public)
            or any(item.nullable for item in self.fields if item.name in self.business_key)
            or any(item.name != item.column and item.name in physical for item in self.fields)
        ):
            raise ValueError("Ambiguous field mapping or business key")
        for relation in self.relationships:
            if any(
                pair.local.startswith("__") or pair.remote.startswith("__")
                for pair in relation.mapping
            ):
                raise ValueError("Reserved relationship column")
            if len(relation.mapping) > 20 or "." not in relation.target_product:
                raise ValueError("Canonical relationships require qualified targets")
            if not {item.local for item in relation.mapping} <= set(physical):
                raise ValueError("Unknown relationship column")
        return self


class ProductCreate(DTO):
    data_source_id: UUID
    product_key: QualifiedKey
    name: str = Field(min_length=1, max_length=200)
    description: Description | None = None
    schema_name: Identifier
    object_name: Identifier
    object_type: Literal["TABLE", "VIEW"]
    classification: Classification


class ContractCreate(DTO):
    version: str = Field(min_length=5, max_length=30, pattern=SEMVER)
    contract_json: DataContractDocument


class ContractPatch(RevisionCommand):
    contract_json: DataContractDocument


class PublishProduct(RevisionCommand):
    contract_version_id: UUID
