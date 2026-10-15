# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Service danh tính mẫu: issuer giả và principal cố định, chưa xác minh JWT.

from flowbridge.identity.schemas import IdentityConfig, Principal
from flowbridge.sample import SampleContext


class SampleIdentityAdapter:
    # Adapter fixture, không gọi Keycloak hay kiểm tra chữ ký token.
    def config(self) -> IdentityConfig:
        # Trả endpoint OIDC mẫu trên domain example.invalid.
        issuer = "https://identity.example.invalid/realms/sample"
        return IdentityConfig(
            issuer=issuer,
            authorization_endpoint=f"{issuer}/protocol/openid-connect/auth",
            token_endpoint=f"{issuer}/protocol/openid-connect/token",
            jwks_uri=f"{issuer}/protocol/openid-connect/certs",
            web_client_id="sample-web",
            mobile_client_id="sample-mobile",
            scopes=["openid", "profile"],
            pkce_required=True,
        )

    def principal(self, context: SampleContext) -> Principal:
        # Trả user mẫu từ context server, không tin sub/scope client gửi.
        return Principal(
            id=context.principal_id,
            tenant_id=context.tenant_id,
            display_name="Sample User",
            roles=["sample_user"],
            permissions=["sample:read", "sample:write"],
        )


class IdentityService:
    # Use case danh tính, chuyển tiếp sang adapter mẫu.
    def __init__(self, adapter: SampleIdentityAdapter) -> None:
        self.adapter = adapter

    def config(self) -> IdentityConfig:
        # Lấy cấu hình issuer mẫu.
        return self.adapter.config()

    def principal(self, context: SampleContext) -> Principal:
        # Lấy principal mẫu theo context server.
        return self.adapter.principal(context)
