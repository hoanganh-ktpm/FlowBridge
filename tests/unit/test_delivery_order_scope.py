# SPDX-License-Identifier: GPL-3.0-or-later
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant import delivery_statistics as module


@pytest.mark.asyncio
@pytest.mark.parametrize("exact_order", [False, True])
@pytest.mark.parametrize(
    "role,account,field",
    [
        ("EMPLOYEE", "human", "requested_by"),
        ("APPROVER", "human", "department_id"),
        ("WAREHOUSE", "human", "department_id"),
        ("PROCUREMENT_VIEWER", "human", "global"),
        ("AI_SERVICE", "service", None),
    ],
)
async def test_interactive_orders_keep_real_database_grants_and_owner_or_department_scope(
    monkeypatch,
    role,
    account,
    field,
    exact_order,
):
    owner, department, role_id, supplier_id = uuid4(), uuid4(), uuid4(), uuid4()
    selected_order = uuid4() if exact_order else None
    scope = "department" if role in {"APPROVER", "WAREHOUSE"} else "global"
    captured = []

    async def rows(name, *conditions, **kwargs):
        if name == "users":
            return [{"user_id": owner, "account_type": account, "status": "active"}]
        if name == "roles":
            return [
                {
                    "role_id": role_id,
                    "role_code": role,
                    "status": "active",
                    "allowed_account_type": account,
                    "scope_type": scope,
                }
            ]
        if name == "user_roles":
            return [
                {
                    "role_id": role_id,
                    "scope_type": scope,
                    "department_id": department if scope == "department" else None,
                }
            ]
        assert name == "purchase_orders"
        captured.extend(conditions)
        return []

    repo = SimpleNamespace(
        rows=rows,
        get=AsyncMock(
            return_value={
                "supplier_id": supplier_id,
                "supplier_code": "NCC-03",
                "supplier_name": "Nam Việt",
            }
        ),
    )
    monkeypatch.setattr(module, "SupplierRepository", lambda _: repo)

    @asynccontextmanager
    async def transaction():
        yield

    session = SimpleNamespace(execute=AsyncMock(), begin=transaction)

    @asynccontextmanager
    async def sessions():
        yield session

    identity = VerifiedIdentity("https://issuer.invalid", "subject", account_type=account)
    if field is None:
        with pytest.raises(CoreError) as error:
            await module.calculate_statistics(sessions, identity, None, supplier_id)
        assert error.value.code == ErrorCode.FORBIDDEN
        assert captured == []
        return
    result = await module.calculate_statistics(
        sessions,
        identity,
        None,
        supplier_id,
        module.DeliveryOrdersRequest(supplier_id=supplier_id, purchase_order_id=selected_order),
    )
    assert result.scope == (
        "company_procurement_reader" if field == "global" else "caller_visible_orders"
    )
    condition = next(c for c in captured if "purchase_requests" in str(c)).compile()
    combined = module.sa.select(module.sa.literal(1)).where(*captured).compile()
    if selected_order:
        assert selected_order in combined.params.values()
        assert "purchase_orders.purchase_order_id =" in str(combined)
    assert result.orders.purchase_order_id == selected_order
    if field == "global":
        assert "WHERE true" in str(condition)
        assert not condition.params
    else:
        assert f"purchase_requests.{field}" in str(condition)
        assert (owner if field == "requested_by" else department) in condition.params.values()
    assert result.orders.total_matches == 0
    assert result.as_of.tzinfo is not None
    session.execute.assert_awaited_once()
