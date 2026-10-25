# SPDX-License-Identifier: GPL-3.0-or-later
from uuid import uuid4

import httpx
import pytest
from workspace_app.connector import CoreConnector
from workspace_app.records import DEFINITIONS
from workspace_app.runtime import PREFIX, create_app
from workspace_app.source_roles import DEFAULT_SOURCE_NAMES

from .test_api import ORG, OWNER, RepositoryBoundary


class Boundary:
    def __init__(self):
        self.products = []
        self.data = {}
        self.reads = []
        self.revoked = self.foreign = self.duplicate = False
        self.missing = False
        for kind, (_, fields) in DEFINITIONS.items():
            identifier, contract = str(uuid4()), str(uuid4())
            grain = {
                "warehouses": "warehouse_id",
                "products": "product_id",
                "orders": "order_id",
                "order_lines": "order_line_id",
            }[kind]
            self.products.append(
                dict(
                    id=identifier,
                    contract_version_id=contract,
                    name=DEFAULT_SOURCE_NAMES["records_" + kind],
                    business_key=["org_id", grain],
                    fields=[{"name": f} for f in ["org_id", *fields]],
                )
            )
            records = []
            for i in range(53 if kind == "orders" else 2):
                row = {f: None for f in fields}
                row.update(org_id=str(ORG), **{grain: str(uuid4())})
                if kind == "orders":
                    row.update(
                        order_number=f"SO-{i:03}",
                        status="COMPLETED" if i % 2 else "CONFIRMED",
                        line_count=2,
                    )
                if kind == "warehouses":
                    row.update(warehouse_code=f"WH-{i}", warehouse_name=f"Kho {i}", status="ACTIVE")
                if kind == "products":
                    row.update(
                        sku=f"SKU-{i}", product_name=f"Sản phẩm {i}", uom="cái", status="ACTIVE"
                    )
                records.append(row)
            self.data[identifier] = records

    def respond(self, request):
        import json

        assert request.headers["Authorization"] == "Bearer caller"
        if request.url.path.endswith("/me"):
            return httpx.Response(
                200,
                json=dict(principal_status="ACTIVE", principal_id=str(OWNER), platform_role="NONE"),
            )
        if request.url.path.endswith("/authorized-data-products"):
            return httpx.Response(200, json={"items": [] if self.missing else self.products})
        body = json.loads(request.content)
        assert body["where"]["org_id"] == {"_eq": str(ORG)}
        self.reads.append(body)
        if self.revoked and body["limit"] == 1:
            return httpx.Response(403)
        product = next(p for p in self.products if p["id"] in request.url.path)
        rows = self.data[product["id"]]
        for key, terms in body["where"].items():
            rows = [
                r
                for r in rows
                if all(op == "_eq" and r.get(key) == value for op, value in terms.items())
            ]
        rows = [dict(r) for r in rows[body["offset"] : body["offset"] + body["limit"]]]
        if rows and self.foreign:
            rows[0]["org_id"] = str(uuid4())
        if len(rows) > 1 and self.duplicate:
            rows[1] = dict(rows[0])
        return httpx.Response(200, json={"rows": rows, "has_more": False})


async def call(boundary, body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(boundary.respond)) as http:
        connector = CoreConnector(http, "http://core.test", ORG)
        app = create_app(connector, RepositoryBoundary())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://workspace"
        ) as client:
            return await client.post(
                PREFIX + "/records", headers={"Authorization": "Bearer caller"}, json=body
            )


@pytest.mark.asyncio
async def test_complete_order_register_pages_past_the_first_page_and_includes_completed():
    b = Boundary()
    first = await call(b, {"collection": "orders"})
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 50 and page["has_more"]
    assert any(r["status"] == "COMPLETED" for r in page["items"])
    second = await call(b, {"collection": "orders", "offset": 50})
    assert len(second.json()["items"]) == 3 and not second.json()["has_more"]
    assert {r["order_id"] for r in page["items"]}.isdisjoint(
        r["order_id"] for r in second.json()["items"]
    )
    assert all(q["where"] == {"org_id": {"_eq": str(ORG)}} for q in b.reads)
    filtered = await call(b, {"collection": "orders", "code": "SO-001", "status": "COMPLETED"})
    assert [r["order_number"] for r in filtered.json()["items"]] == ["SO-001"]


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", ["warehouses", "products"])
async def test_register_does_not_depend_on_inventory_balances_or_forecast_history(collection):
    response = await call(Boundary(), {"collection": collection})
    assert response.status_code == 200 and len(response.json()["items"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flag,status", [("missing", 403), ("revoked", 403), ("foreign", 422), ("duplicate", 502)]
)
async def test_register_discards_missing_permission_revocation_foreign_and_duplicate_rows(
    flag, status
):
    b = Boundary()
    setattr(b, flag, True)
    response = await call(b, {"collection": "orders"})
    assert response.status_code == status, response.text
    assert "items" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"collection": "orders", "org_id": str(uuid4())},
        {"collection": "order_lines"},
        {"collection": "orders", "offset": -1},
        {"collection": "orders", "order_id": str(uuid4())},
    ],
)
async def test_register_scope_and_filters_are_bounded(body):
    assert (await call(Boundary(), body)).status_code == 422
