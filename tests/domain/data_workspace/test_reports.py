# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import date

import httpx
import pytest
from fastapi import HTTPException
from workspace_app.connector import CoreConnector
from workspace_app.reports import overview
from workspace_app.runtime import PREFIX, create_app
from workspace_app.schemas import OverviewRequest

from .test_api import ORG, CoreBoundary, RepositoryBoundary
from .test_pipeline import config, rows


def source():
    cfg = config()
    return {
        "name": "logistics.accepted_shipments_daily",
        "fields": [{"name": n} for n in ["day", "carrier_code", "accepted_shipments"]],
    }, OverviewRequest(
        data_product_id=cfg.data_product_id,
        contract_version_id=cfg.contract_version_id,
        entity="C1",
    )


def test_operational_report_explains_sparse_event_days_and_preserves_source_age():
    product, body = source()
    data = [
        {"day": "2026-01-01", "carrier_code": "C1", "accepted_shipments": 2},
        {"day": "2026-01-03", "carrier_code": "C1", "accepted_shipments": 4},
        {"day": "2026-01-04", "carrier_code": "C2", "accepted_shipments": 1},
    ]
    result = overview(product, data, body, date(2026, 1, 10))
    assert result["entities"] == ["C1", "C2"]
    assert result["source_end"] == date(2026, 1, 4)
    assert result["source_age_days"] == 6
    assert [p["target"] for p in result["analytics"]["series"]] == [2, 0, 4, 0]
    assert result["analytics"]["quality"]["missing_periods"] == 2
    assert result["analytics"]["metrics"][0]["total"] == 6
    assert result["config"]["forecast_start"] == "2026-01-10"
    assert (
        result["analytics"]["evidence"]["report_policy"]["method"] == "published_event_aggregate_v1"
    )


@pytest.mark.parametrize(
    "data", [[], [{"day": "2026-01-10", "carrier_code": "C1", "accepted_shipments": 2}]]
)
def test_absent_or_incomplete_today_is_not_replaced_with_invented_zero_data(data):
    product, body = source()
    with pytest.raises(HTTPException) as error:
        overview(product, data, body, date(2026, 1, 10))
    assert error.value.detail["code"] == "NO_DATA"


def test_arbitrary_product_cannot_inherit_known_event_zero_policy():
    product, body = source()
    product["name"] = "other.measurements"
    with pytest.raises(HTTPException) as error:
        overview(product, [], body)
    assert error.value.detail["code"] == "UNSUPPORTED_REPORT"


def test_old_sources_remain_inspectable_but_do_not_invent_a_forecast_origin():
    product, body = source()
    result = overview(
        product,
        [{"day": "2026-01-01", "carrier_code": "C1", "accepted_shipments": 2}],
        body,
        date(2026, 10, 25),
    )
    assert result["config"]["forecast_start"] is None
    assert result["source_age_days"] > 90


@pytest.mark.asyncio
async def test_overview_http_rechecks_permissions_and_never_turns_outage_into_zero():
    core = CoreBoundary()
    core.cfg = config(target_column="confirmed_quantity")
    core.data = [{**r, "org_id": str(ORG)} for r in rows(core.cfg)]

    def respond(request):
        response = core.respond(request)
        if response.status_code == 200 and request.url.path.endswith("/authorized-data-products"):
            data = response.json()
            for p in data["items"]:
                p["fields"] = [{"name": n} for n in ["org_id", "sku", "day", "confirmed_quantity"]]
            return httpx.Response(200, json=data)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
        app = create_app(CoreConnector(upstream, "https://core.test", ORG), RepositoryBoundary())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://workspace"
        ) as client:
            body = {
                "data_product_id": str(core.cfg.data_product_id),
                "contract_version_id": str(core.cfg.contract_version_id),
            }
            assert (await client.post(PREFIX + "/overview", json=body)).status_code == 401
            client.headers["Authorization"] = "Bearer caller"
            result = await client.post(PREFIX + "/overview", json=body)
            assert result.status_code == 200, result.text
            assert result.json()["entity"] == "A"
            core.allowed = False
            assert (
                await client.post(
                    PREFIX + "/overview", json=body, headers={"X-Role": "PLATFORM_ADMIN"}
                )
            ).status_code == 403
            core.available = False
            assert (await client.post(PREFIX + "/overview", json=body)).status_code == 503
