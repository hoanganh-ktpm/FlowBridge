from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.data_products.credentials import HasuraJwtCredentials
from app.core.data_products.reading import TrustedHasuraContext
from app.platform.http.errors import CoreError


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds,allowed", [(0.5, True), (2, False), (-11, False)])
async def test_trusted_database_clock_tolerance_is_bounded(seconds, allowed):
    context = TrustedHasuraContext(
        *(uuid4() for _ in range(5)), 1, 1, datetime.now(UTC) + timedelta(seconds=seconds), None
    )
    issuer = HasuraJwtCredentials(SecretStr("local-test-signing-key-" * 3), "issuer", "audience")
    if allowed:
        assert (await issuer.issue(context)).get_secret_value()
    else:
        with pytest.raises(CoreError):
            await issuer.issue(context)
