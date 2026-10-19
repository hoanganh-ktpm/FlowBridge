# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact code search remains bound by the existing request read policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import sqlalchemy as sa

from flowbridge.supplier_assistant.contract import application_operations
from flowbridge.supplier_assistant.database_service import SupplierDatabaseService


@pytest.mark.asyncio
@pytest.mark.parametrize("company_reader", [False, True])
@pytest.mark.parametrize(
    "op,name,code",
    [
        ("listPurchaseOrders", "purchase_orders", "po_number"),
        ("listPurchaseRequests", "purchase_requests", "request_code"),
    ],
)
async def test_exact_code_is_bound_equality_and_preserves_read_scope(
    company_reader, op, name, code
):
    page = {"items": [], "total": 0}
    repo = SimpleNamespace(page=AsyncMock(return_value=page))
    service = SupplierDatabaseService(repo)
    service.policy.user = {"user_id": uuid4(), "account_type": "human"}
    service.policy.grants = {"EMPLOYEE": [{}]}
    if company_reader:
        service.policy.grants["PROCUREMENT_VIEWER"] = [{}]
    service.policy.authorize("EMPLOYEE", read=True)
    # Wildcard characters must remain literal, not turn into a LIKE/SQL expression.
    value = " demo-po-00656%' "
    result = await service.dispatch(op, {}, {}, {code: value, "page_size": 2}, None, None)
    assert result == page
    target, conditions, page_no, size = repo.page.await_args.args
    assert (target, page_no, size) == (name, 1, 2)
    query = sa.select(sa.literal(1)).where(*conditions).compile()
    assert f"upper({name}.{code}) =" in str(query)
    assert value.strip().upper() in query.params.values()
    assert value not in str(query) and "LIKE" not in str(query)
    if company_reader:
        assert "requested_by" not in str(query)
    else:
        assert "purchase_requests.requested_by" in str(query)
        assert service.policy.user["user_id"] in query.params.values()


def test_code_filters_are_served_and_parsed_from_same_contract_registry():
    operations = {spec["operationId"]: spec for _, _, spec in application_operations()}
    for op, code in (("listPurchaseOrders", "po_number"), ("listPurchaseRequests", "request_code")):
        parameter = next(p for p in operations[op]["parameters"] if p["name"] == code)
        assert parameter["in"] == "query"
        assert parameter["schema"]["maxLength"] == 255
