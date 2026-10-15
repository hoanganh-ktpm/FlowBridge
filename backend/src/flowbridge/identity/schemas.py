# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# DTO danh tính: cấu hình issuer và principal mẫu.

from typing import Literal

from pydantic import AnyHttpUrl

from flowbridge.shared.schemas import DTO, Id


class IdentityConfig(DTO):
    # Endpoint OIDC mẫu và client công khai, không chứa secret.
    issuer: AnyHttpUrl
    authorization_endpoint: AnyHttpUrl
    token_endpoint: AnyHttpUrl
    jwks_uri: AnyHttpUrl
    web_client_id: str
    mobile_client_id: str
    scopes: list[str]
    pkce_required: Literal[True]


class Principal(DTO):
    # Danh tính mẫu gồm tenant/role/permission cố định.
    id: Id
    tenant_id: Id
    display_name: str
    roles: list[str]
    permissions: list[str]
