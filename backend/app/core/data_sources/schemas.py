from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.applications.manifest import SecretReference
from app.core.governance.schemas import QualifiedKey
from app.core.hasura_provisioner.schemas import DTO


class SourceCreate(DTO):
    application_id: UUID
    source_key: QualifiedKey
    db_type: Literal["POSTGRESQL"]
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
    port: int = Field(ge=1, le=65535, strict=True)
    database_name: str = Field(min_length=1, max_length=63, pattern=r"^[A-Za-z0-9_-]+$")
    db_user: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    secret_ref: SecretReference
    ssl_mode: Literal["verify-full", "verify-ca", "require"] = "verify-full"
