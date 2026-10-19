# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from flowbridge.supplier_assistant import database_service as module
from flowbridge.supplier_assistant.repository import table


@pytest.mark.asyncio
@pytest.mark.parametrize("company_reader", [False, True])
async def test_multi_supplier_groups_filter_before_paging_and_preserve_request_scope(
    monkeypatch, company_reader
):
    owner, other = uuid4(), uuid4()
    request_ids = [uuid4() for _ in range(4)]
    s1, s2 = uuid4(), uuid4()
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
        sa.Column("supplier_id", sa.Uuid),
        sa.Column("status", sa.String),
    )
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            requests.insert(),
            [
                {"request_id": r, "requested_by": other if i == 2 else owner}
                for i, r in enumerate(request_ids)
            ],
        )
        conn.execute(
            orders.insert(),
            [
                {
                    "purchase_order_id": uuid4(),
                    "request_id": r,
                    "supplier_id": s,
                    "status": "cancelled" if i == 3 and j == 1 else "issued",
                }
                for i, r in enumerate(request_ids)
                for j, s in enumerate([s1, s1] if i == 0 else [s1, s2])
            ],
        )

        async def page(name, conditions, page, size):
            assert name == "purchase_requests" and (page, size) == (1, 1)
            ids = list(
                conn.execute(sa.select(table(name).c.request_id).where(*conditions)).scalars()
            )
            return {"items": [{"request_id": str(r)} for r in ids[:size]], "total": len(ids)}

        async def rows(name, *conditions, **kw):
            assert name == "purchase_orders" and kw["limit"] == 101
            t = table(name)
            return [
                dict(r)
                for r in conn.execute(
                    sa.select(
                        t.c.purchase_order_id, t.c.request_id, t.c.supplier_id, t.c.status
                    ).where(*conditions)
                ).mappings()
            ]

        monkeypatch.setattr(
            module,
            "dto",
            lambda name, row: {k: str(v) if isinstance(v, UUID) else v for k, v in row.items()},
        )
        service = module.SupplierDatabaseService(SimpleNamespace(page=page, rows=rows))
        service.policy.user = {"user_id": owner, "account_type": "human"}
        service.policy.grants = {"EMPLOYEE": [{}]}
        if company_reader:
            service.policy.grants["PROCUREMENT_VIEWER"] = [{}]
        service.policy.authorize("EMPLOYEE", read=True)
        result = await service.dispatch(
            "listPurchaseRequests",
            {},
            {},
            {
                "min_distinct_suppliers": 2,
                "page_size": 1,
            },
            None,
            None,
        )
        assert result["total"] == (2 if company_reader else 1)
        assert result["min_distinct_suppliers"] == 2
        group = result["items"][0]
        assert UUID(group["request_id"]) not in [request_ids[0], request_ids[3]]
        assert group["distinct_supplier_count"] == 2
        assert {o["request_id"] for o in group["purchase_orders"]} == {group["request_id"]}
        if not company_reader:
            assert group["request_id"] == str(request_ids[1])
    engine.dispose()
