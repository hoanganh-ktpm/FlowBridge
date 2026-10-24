"""Short-lived internal Hasura credentials derived only from trusted Core context."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

from pydantic import SecretStr

from app.platform.http.errors import CoreError, ErrorCode


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class HasuraJwtCredentials:
    def __init__(self, key: SecretStr, issuer: str, audience: str):
        if len(key.get_secret_value().encode()) < 32 or not issuer or not audience:
            raise ValueError("Explicit HS256 key, issuer and audience are required")
        self.key, self.issuer, self.audience = key, issuer, audience

    async def issue(self, context) -> SecretStr:
        now = datetime.now(UTC)
        if (
            context.applied_revision != context.desired_revision
            # Trusted DB validation and the native issuer may differ slightly
            # across Docker/host clocks. Tokens still use the issuer's current
            # time and retain their ten-second lifetime.
            or not -1 <= (now - context.validated_at).total_seconds() <= 10
        ):
            raise CoreError(ErrorCode.FORBIDDEN)
        expires = int(now.timestamp()) + 10
        if context.expires_at is not None:
            expires = min(expires, int(context.expires_at.timestamp()))
        if expires <= int(now.timestamp()):
            raise CoreError(ErrorCode.FORBIDDEN)
        payload = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": str(context.principal_id),
            "iat": int(now.timestamp()),
            "exp": expires,
            "https://hasura.io/jwt/claims": {
                "x-hasura-default-role": context.enforcement_role,
                "x-hasura-allowed-roles": [context.enforcement_role],
                "x-hasura-user-id": str(context.principal_id),
                "x-hasura-department-id": str(context.department_id),
            },
        }
        header = encode(b'{"alg":"HS256","typ":"JWT"}')
        body = encode(json.dumps(payload, separators=(",", ":")).encode())
        unsigned = header + "." + body
        signature = hmac.new(
            self.key.get_secret_value().encode(), unsigned.encode(), hashlib.sha256
        )
        return SecretStr(unsigned + "." + encode(signature.digest()))
