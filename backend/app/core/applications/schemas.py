from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.governance.schemas import Description, LogicalKey, RevisionCommand
from app.core.hasura_provisioner.schemas import DTO


class ApplicationCreate(DTO):
    department_id: UUID
    app_key: LogicalKey
    name: str = Field(min_length=1, max_length=200)
    description: Description | None = None
    business_api_base_url: str | None = None

    @field_validator("business_api_base_url")
    @classmethod
    def https_url(cls, value: str | None) -> str | None:
        from urllib.parse import urlsplit

        if value is not None:
            parts = urlsplit(value)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
                or any(character.isspace() for character in value)
            ):
                raise ValueError("HTTPS URL required")
        return value


class ApplicationPatch(RevisionCommand):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: Description | None = None
    business_api_base_url: str | None = None
    status: Literal["DRAFT", "ACTIVE", "DISABLED"] | None = None

    _https_url = field_validator("business_api_base_url")(ApplicationCreate.https_url.__func__)

    @model_validator(mode="before")
    @classmethod
    def nonempty_patch(cls, value):
        if isinstance(value, dict) and (
            len(value) < 2
            or any(name in value and value[name] is None for name in ("name", "status"))
        ):
            raise ValueError("Patch requires a non-null change")
        return value
