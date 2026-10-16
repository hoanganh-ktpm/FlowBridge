# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Public core DTOs; token metadata and policy context stay internal.
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
)

_URI_ADAPTER = TypeAdapter(AnyUrl)


def validate_uri(value: str) -> str:
    try:
        _URI_ADAPTER.validate_python(value)
    except ValueError:
        raise ValueError("Expected an absolute URI") from None
    return value


Uri = Annotated[str, AfterValidator(validate_uri), Field(json_schema_extra={"format": "uri"})]

CoreId = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
]


class IdentityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    issuer: Uri
    authorization_endpoint: Uri
    token_endpoint: Uri
    jwks_uri: Uri
    web_client_id: str
    mobile_client_id: str
    scopes: list[str]
    pkce_required: Literal[True]


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: CoreId
    tenant_id: CoreId
    display_name: str
    roles: list[str]
    permissions: list[str]
