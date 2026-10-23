import os

import httpx
import pytest

from app.core.hasura_provisioner import HasuraMetadataClient, HasuraSettings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_existing_hasura_health_and_metadata():
    if os.environ.get("FLOWBRIDGE_HASURA_LIVE_TESTS") != "1":
        pytest.skip("PENDING INTEGRATION: explicitly enable an existing Hasura instance")
    if not all(
        os.environ.get(name)
        for name in ("FLOWBRIDGE_HASURA_ENDPOINT", "FLOWBRIDGE_HASURA_ADMIN_SECRET")
    ):
        pytest.skip("PENDING INTEGRATION: missing Hasura endpoint/admin secret")
    settings = HasuraSettings(_env_file=None)
    async with httpx.AsyncClient(trust_env=False) as http:
        client = HasuraMetadataClient(settings, http)
        assert await client.check_health()
        assert (await client.export_metadata()).metadata["version"] == 3
        assert (await client.get_inconsistent_metadata()).is_consistent
