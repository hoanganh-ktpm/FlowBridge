# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Money outside NUMERIC(18,4) must produce a client error instead of decimal/SQL failures.
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.database_service import amount
from flowbridge.supplier_assistant.policy import SupplierPolicy


@pytest.mark.parametrize("value", ["10000000000000000000000000000", "-0.0001", "NaN", "Infinity"])
def test_out_of_range_money_is_validation_error(value: str) -> None:
    with pytest.raises(CoreError) as failure:
        amount(Decimal(value))
    assert failure.value.code == ErrorCode.VALIDATION_ERROR


def test_valid_money_retains_four_digit_boundary_and_half_up_rounding() -> None:
    assert amount(Decimal("99999999999999.9999")) == Decimal("99999999999999.9999")
    assert amount(Decimal("1.23455")) == Decimal("1.2346")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["EMPLOYEE", "APPROVER", "WAREHOUSE", "ADMIN"])
async def test_human_profile_accepts_existing_grants_without_admin_purchase_access(role) -> None:
    scope = "department" if role in {"APPROVER", "WAREHOUSE"} else "global"
    repository = AsyncMock()
    repository.rows.side_effect = [
        [{"user_id": "user", "account_type": "human", "status": "active"}],
        [
            {
                "role_id": "role",
                "role_code": role,
                "status": "active",
                "allowed_account_type": "human",
                "scope_type": scope,
            }
        ],
        [
            {
                "role_id": "role",
                "scope_type": scope,
                "department_id": "department" if scope == "department" else None,
            }
        ],
    ]
    policy = SupplierPolicy(repository)
    await policy.authenticate(VerifiedIdentity("https://identity.example", "subject"))
    policy.authorize_profile()
    if role == "ADMIN":
        with pytest.raises(CoreError) as failure:
            policy.authorize("EMPLOYEE", read=True)
        assert failure.value.code == ErrorCode.FORBIDDEN


@pytest.mark.parametrize(
    "account,grants",
    [
        ("human", {}),
        ("service", {"AI_SERVICE": [{}]}),
        ("service", {"PO_SERVICE": [{}]}),
        ("service", {"ADMIN": [{}]}),
    ],
)
def test_profile_denies_missing_human_grants_and_service_accounts(account, grants) -> None:
    policy = SupplierPolicy(AsyncMock())
    policy.user = {"account_type": account}
    policy.grants = grants
    with pytest.raises(CoreError) as failure:
        policy.authorize_profile()
    assert failure.value.code == ErrorCode.FORBIDDEN
