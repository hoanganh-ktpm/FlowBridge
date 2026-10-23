import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.data_products.credentials import HasuraJwtCredentials
from app.core.data_products.reading import TrustedHasuraContext
from app.core.db.engine import core_engine
from app.core.runtime import RuntimeSettings
from app.platform.http.errors import CoreError


def context():
    return TrustedHasuraContext(
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), 1, 1, datetime.now(UTC), None
    )


@pytest.mark.asyncio
async def test_internal_hasura_token_has_one_trusted_role_and_short_expiry():
    key = "SYNTHETIC_TEST_SIGNING_KEY_NOT_PRODUCTION"
    provider = HasuraJwtCredentials(SecretStr(key), "core-test", "hasura-test")
    current = context()
    token = (await provider.issue(current)).get_secret_value()
    header, body, signature = token.split(".")
    expected = hmac.new(key.encode(), (header + "." + body).encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(signature + "==") == expected
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    assert claims["https://hasura.io/jwt/claims"]["x-hasura-allowed-roles"] == [
        current.enforcement_role
    ]
    assert 0 < claims["exp"] - claims["iat"] <= 10
    assert claims["iss"] == "core-test" and claims["aud"] == "hasura-test"


@pytest.mark.asyncio
async def test_expired_grant_cannot_get_internal_token():
    from dataclasses import replace

    provider = HasuraJwtCredentials(
        SecretStr("SYNTHETIC_TEST_SIGNING_KEY_NOT_PRODUCTION"), "core", "data"
    )
    with pytest.raises(CoreError):
        await provider.issue(
            replace(context(), expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )


def test_runtime_does_not_enable_unverified_hasura_profile():
    settings = RuntimeSettings(
        _env_file=None, FLOWBRIDGE_CORE_DATABASE_URL="postgresql://test@localhost/core"
    )
    assert not settings.verified_hasura_auth_profile
    assert "postgresql://" not in repr(settings)


@pytest.mark.parametrize(
    "value",
    [
        "sqlite:///test.db",
        "postgresql://user@remote.invalid/db?sslmode=disable",
        "postgresql://user@project.pooler.supabase.com:6543/db?sslmode=require",
    ],
)
def test_core_engine_refuses_unsafe_connection_profiles(value):
    with pytest.raises(ValueError):
        core_engine(value)
