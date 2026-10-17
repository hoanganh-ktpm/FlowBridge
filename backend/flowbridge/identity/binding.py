# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# One deployment binds exactly one database, issuer, tenant and application.
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flowbridge.identity.schemas import CoreId


class ServiceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(min_length=1)
    permissions: frozenset[str] = frozenset()


class IdentityBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: CoreId
    application_id: CoreId
    web_client_id: str = Field(min_length=1)
    mobile_client_id: str = Field(min_length=1)
    service_clients: dict[str, ServiceBinding] = Field(default_factory=dict)
    # An explicit allowlist, never ADMIN -> wildcard. Resource permissions need a receiver policy.
    role_permissions: dict[str, frozenset[str]] = Field(default_factory=dict)

    @field_validator("web_client_id", "mobile_client_id")
    @classmethod
    def client_id(cls, value: str) -> str:
        if value != value.strip() or any(c.isspace() for c in value):
            raise ValueError("Client IDs must not contain whitespace")
        return value

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        human = {self.web_client_id, self.mobile_client_id}
        if len(human) != 2 or human & self.service_clients.keys():
            raise ValueError("Public clients must be distinct from each other and service clients")
        for mapping in (self.role_permissions, self.service_clients):
            if any(not key or key != key.strip() for key in mapping):
                raise ValueError("Binding keys must be nonblank without surrounding whitespace")
        scopes = [p for values in self.role_permissions.values() for p in values]
        scopes.extend(p for service in self.service_clients.values() for p in service.permissions)
        if any(not p or any(c.isspace() for c in p) or "*" in p for p in scopes):
            raise ValueError("Permissions must be explicit OAuth scope names without wildcards")
        return self
