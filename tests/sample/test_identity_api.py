# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Test identity mẫu: config/principal gọi được và bỏ qua header giả.

import httpx
import pytest

from flowbridge.sample import PRINCIPAL_ID, TENANT_ID

from .test_contract_coverage import validate_response


@pytest.mark.asyncio
async def test_identity_sample_is_callable_and_ignores_forged_identity(
    client: httpx.AsyncClient,
) -> None:
    config = await client.get("/api/v1/core/identity/config")
    assert config.status_code == 200
    validate_response(config, "IdentityConfig")
    assert config.json()["pkce_required"] is True
    assert "example.invalid" in config.json()["issuer"]
    me = await client.get(
        "/api/v1/core/identity/me",
        headers={"Authorization": "Bearer forged", "X-Tenant-Id": "OTHER", "X-User-Id": "OTHER"},
    )
    assert me.status_code == 200
    validate_response(me, "Principal")
    assert me.json()["id"] == PRINCIPAL_ID
    assert me.json()["tenant_id"] == TENANT_ID
    assert "secret" not in config.text
