# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from flowbridge.supplier_assistant.contract import application_operations, dto_validator
from flowbridge.supplier_assistant.database_service import SupplierDatabaseService
from flowbridge.supplier_assistant.repository import table


@pytest.mark.asyncio
@pytest.mark.parametrize("company_reader", [False, True])
async def test_exact_parent_filter_runs_before_count_page_and_retains_caller_scope(company_reader):
    owner, other = uuid4(), uuid4()
    target, unrelated = uuid4(), uuid4()
    selected, not_selected = uuid4(), uuid4()
    meta = sa.MetaData()
    requests = sa.Table(
        "purchase_requests",
        meta,
        sa.Column("request_id", sa.Uuid, primary_key=True),
        sa.Column("requested_by", sa.Uuid),
    )
    orders = sa.Table(
        "purchase_orders",
        meta,
        sa.Column("purchase_order_id", sa.Uuid, primary_key=True),
        sa.Column("request_id", sa.Uuid),
    )
    engine = sa.create_engine("sqlite://")
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            requests.insert(),
            [
                {"request_id": target, "requested_by": other},
                {"request_id": unrelated, "requested_by": owner},
            ],
        )
        conn.execute(
            orders.insert(),
            [
                {"purchase_order_id": selected, "request_id": target},
                {"purchase_order_id": not_selected, "request_id": unrelated},
            ],
        )

        async def page(name, conditions, page, size):
            assert (name, page, size) == ("purchase_orders", 1, 10)
            rows = list(
                conn.execute(
                    sa.select(table(name).c.purchase_order_id).where(*conditions)
                ).scalars()
            )
            return {"items": rows, "total": len(rows)}

        service = SupplierDatabaseService(SimpleNamespace(page=page))
        service.policy.user = {"user_id": owner, "account_type": "human"}
        service.policy.grants = {"EMPLOYEE": [{}]}
        if company_reader:
            service.policy.grants["PROCUREMENT_VIEWER"] = [{}]
        service.policy.authorize("EMPLOYEE", read=True)
        result = await service.dispatch(
            "listPurchaseOrders", {}, {}, {"request_id": str(target), "page_size": 10}, None, None
        )
        assert result["items"] == ([selected] if company_reader else [])
        assert result["total"] == (1 if company_reader else 0)
        assert result["request_id"] == str(target)
    engine.dispose()


def test_parent_filter_and_echo_share_runtime_contract():
    spec = next(
        s for _, _, s in application_operations() if s["operationId"] == "listPurchaseOrders"
    )
    parameter = next(p for p in spec["parameters"] if p["name"] == "request_id")
    assert parameter["schema"] == {"type": "string", "format": "uuid"}
    dto_validator("PurchaseOrderPage").validate(
        {"items": [], "total": 0, "page": 1, "page_size": 10, "request_id": str(uuid4())}
    )
