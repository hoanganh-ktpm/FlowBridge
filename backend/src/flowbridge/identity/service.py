# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Resolve bound principals, intersect token capabilities with live DB grants.
from dataclasses import dataclass
from typing import Literal

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.identity.directory import KeycloakDirectory
from flowbridge.identity.repository import IdentityRepository
from flowbridge.identity.schemas import IdentityConfig, Principal
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    principal: Principal
    application_id: str
    account_type: Literal["human", "service"]
    client_id: str

    def require(self, permission: str) -> None:
        if permission not in self.principal.permissions:
            raise CoreError(ErrorCode.FORBIDDEN)


def identity_config(settings: Settings) -> IdentityConfig:
    binding = settings.core_identity
    if binding is None or settings.oidc_issuer is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    issuer = settings.oidc_issuer.rstrip("/")
    return IdentityConfig(
        issuer=settings.oidc_issuer,
        authorization_endpoint=issuer + "/protocol/openid-connect/auth",
        token_endpoint=issuer + "/protocol/openid-connect/token",
        jwks_uri=issuer + "/protocol/openid-connect/certs",
        web_client_id=binding.web_client_id,
        mobile_client_id=binding.mobile_client_id,
        scopes=["openid", "identity:read"],
        pkce_required=True,
    )


class IdentityService:
    def __init__(
        self,
        settings: Settings,
        repository: IdentityRepository,
        directory: KeycloakDirectory,
    ) -> None:
        self.settings, self.repository, self.directory = settings, repository, directory

    async def resolve(
        self,
        identity: VerifiedIdentity,
        *,
        kind: Literal["human", "service"],
        permission: str,
    ) -> PrincipalContext:
        binding = self.settings.core_identity
        if binding is None:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        if identity.issuer != self.settings.oidc_issuer or identity.account_type != kind:
            raise CoreError(ErrorCode.FORBIDDEN)
        if kind == "human":
            if identity.client_id not in {binding.web_client_id, binding.mobile_client_id}:
                raise CoreError(ErrorCode.FORBIDDEN)
        elif identity.client_id not in binding.service_clients:
            raise CoreError(ErrorCode.FORBIDDEN)
        if permission not in identity.scopes:
            raise CoreError(ErrorCode.FORBIDDEN)
        record = await self.repository.resolve(identity.issuer, identity.subject)
        if record.account_type != kind:
            raise CoreError(ErrorCode.FORBIDDEN)
        permissions: set[str] = set()
        role_names: set[str] = set()
        for grant in record.grants:
            configured = binding.role_permissions.get(grant.role, frozenset())
            suffix = f"@{grant.department_id}" if grant.department_id else ""
            role_names.add(f"{binding.application_id}:{grant.role}{suffix}")
            # A department grant is not a global data/workflow capability.
            permissions.update(
                configured if grant.department_id is None else configured & {"identity:read"}
            )
        if kind == "service":
            registered = binding.service_clients[identity.client_id]
            if not any(
                g.role == registered.role and g.department_id is None for g in record.grants
            ):
                raise CoreError(ErrorCode.FORBIDDEN)
            directory_identity = await self.directory.resolve(identity.subject)
            if (
                directory_identity.account_type != "service"
                or directory_identity.client_id != identity.client_id
            ):
                raise CoreError(ErrorCode.FORBIDDEN)
            permissions &= registered.permissions
        principal = Principal(
            id=record.id,
            tenant_id=binding.tenant_id,
            display_name=record.display_name,
            roles=sorted(role_names),
            permissions=sorted(permissions & identity.scopes),
        )
        result = PrincipalContext(principal, binding.application_id, kind, identity.client_id)
        result.require(permission)
        return result
