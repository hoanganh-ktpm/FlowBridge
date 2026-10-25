# SPDX-License-Identifier: GPL-3.0-or-later
import httpx
import pytest
from workspace_app.runtime import PREFIX, create_app

from .test_api import RepositoryBoundary
from .test_decision_workspace import PlanningBoundary


@pytest.mark.asyncio
async def test_directory_has_real_warehouse_and_product_names_without_business_quantities():
    boundary = PlanningBoundary()
    app = create_app(boundary, RepositoryBoundary())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://workspace"
    ) as client:
        response = await client.post(
            PREFIX + "/planning-options",
            headers={"Authorization": "Bearer caller"},
            json={k: boundary.refs[k] for k in ["stock", "catalog"]},
        )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["warehouses"] == [{"id": boundary.wh, "name": "Kho Hà Nội", "code": "HN"}]
    assert result["products"][0]["name"] == "Cà phê rang"
    assert {"on_hand", "available", "reserved", "forecast_quantity"}.isdisjoint(
        result["products"][0]
    )
    assert set(boundary.reads) == {
        boundary.refs[k]["data_product_id"] for k in ["stock", "catalog"]
    }
    assert set(boundary.checked) == set(boundary.reads)


@pytest.mark.asyncio
async def test_directory_rechecks_revocation_and_never_selects_an_unshared_warehouse():
    boundary = PlanningBoundary()
    catalog_id = boundary.refs["catalog"]["data_product_id"]
    boundary.facts[catalog_id].append(
        {
            **boundary.facts[catalog_id][0],
            "warehouse_id": boundary.other,
            "warehouse_name": "Other warehouse",
        }
    )
    app = create_app(boundary, RepositoryBoundary())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://workspace"
    ) as client:
        body = {k: boundary.refs[k] for k in ["stock", "catalog"]}
        headers = {"Authorization": "Bearer caller"}
        response = await client.post(PREFIX + "/planning-options", headers=headers, json=body)
        assert [w["id"] for w in response.json()["warehouses"]] == [boundary.wh]
        boundary.revoked = boundary.refs["stock"]["data_product_id"]
        assert (
            await client.post(PREFIX + "/planning-options", headers=headers, json=body)
        ).status_code == 403
