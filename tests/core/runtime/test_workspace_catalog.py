# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from .fakes import granted


@pytest.mark.asyncio
async def test_catalog_requires_current_applied_grant_and_no_admin_bypass(harness):
    _, _, product, contract, grant = await granted(harness)
    path = "/authorized-data-products"
    assert (await harness.request("GET", path, actor="consumer")).json()["items"] == []
    assert await harness.worker.run_once("catalog-worker")
    result = await harness.request("GET", path, actor="consumer")
    assert result.status_code == 200
    item = result.json()["items"][0]
    assert item["id"] == product["id"] and item["contract_version_id"] == contract["id"]
    assert {f["name"] for f in item["fields"]} == {"sku", "available"}
    assert (await harness.request("GET", path, actor="admin")).json()["items"] == []
    revoked = await harness.request(
        "POST",
        f"/access-grants/{grant['id']}/revoke",
        json={
            "expected_desired_revision": 1,
            "reason": "End analytical access",
        },
    )
    assert revoked.status_code == 202
    assert (await harness.request("GET", path, actor="consumer")).json()["items"] == []
