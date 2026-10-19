# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from flowbridge.supplier_assistant.contract import application_documents
from flowbridge.supplier_assistant.database_service import SupplierDatabaseService
from flowbridge.supplier_assistant.repository import table


@pytest.mark.asyncio
@pytest.mark.parametrize("company_reader", [False, True])
async def test_distinct_product_sql_filter_runs_before_page_and_retains_scope(company_reader):
    # Execute the actual service predicates against minimal SQLite tables.
    # Repeated lines for one product and separate orders must not count as a multi-product PO.
    owner, stranger = uuid4(), uuid4()
    own_request, foreign_request = uuid4(), uuid4()
    single, multiple, hidden = uuid4(), uuid4(), uuid4()
    p1, p2 = uuid4(), uuid4()
    metadata = sa.MetaData()
    requests = sa.Table(
        "purchase_requests",
        metadata,
        sa.Column("request_id", sa.Uuid, primary_key=True),
        sa.Column("requested_by", sa.Uuid),
    )
    orders = sa.Table(
        "purchase_orders",
        metadata,
        sa.Column("purchase_order_id", sa.Uuid, primary_key=True),
        sa.Column("request_id", sa.Uuid),
    )
    lines = sa.Table(
        "purchase_order_items",
        metadata,
        sa.Column("purchase_order_id", sa.Uuid),
        sa.Column("product_id", sa.Uuid),
    )
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            requests.insert(),
            [
                {"request_id": own_request, "requested_by": owner},
                {"request_id": foreign_request, "requested_by": stranger},
            ],
        )
        conn.execute(
            orders.insert(),
            [
                {"purchase_order_id": single, "request_id": own_request},
                {"purchase_order_id": multiple, "request_id": own_request},
                {"purchase_order_id": hidden, "request_id": foreign_request},
            ],
        )
        conn.execute(
            lines.insert(),
            [
                {"purchase_order_id": po, "product_id": product}
                for po, products in [
                    (single, [p1, p1, p1]),
                    (multiple, [p1, p2]),
                    (hidden, [p1, p2]),
                ]
                for product in products
            ],
        )

        async def page(name, conditions, page, size):
            assert name == "purchase_orders" and (page, size) == (1, 1)
            query = sa.select(table(name).c.purchase_order_id).where(*conditions)
            ids = list(conn.execute(query).scalars())
            return {"items": ids[:size], "total": len(ids)}

        service = SupplierDatabaseService(SimpleNamespace(page=page))
        service.policy.user = {"user_id": owner, "account_type": "human"}
        service.policy.grants = {"EMPLOYEE": [{}]}
        if company_reader:
            service.policy.grants["PROCUREMENT_VIEWER"] = [{}]
        service.policy.authorize("EMPLOYEE", read=True)
        result = await service.dispatch(
            "listPurchaseOrders",
            {},
            {},
            {
                "min_distinct_products": 2,
                "page_size": 1,
            },
            None,
            None,
        )
        assert result["total"] == (2 if company_reader else 1)
        assert single not in result["items"]
        assert result["min_distinct_products"] == 2
        if not company_reader:
            assert result["items"] == [multiple]
    engine.dispose()


def test_filter_echo_is_a_shared_optional_dto_extension():
    app, ai = application_documents()
    assert app["components"]["schemas"] == ai["components"]["schemas"]
    schema = app["components"]["schemas"]["PurchaseOrderPage"]
    assert "min_distinct_products" in schema["properties"]
    assert "min_distinct_products" not in schema.get("required", [])
