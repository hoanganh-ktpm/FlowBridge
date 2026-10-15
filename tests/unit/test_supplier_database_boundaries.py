# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Money outside NUMERIC(18,4) must produce a client error instead of decimal/SQL failures.
from decimal import Decimal

import pytest

from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.database_service import amount


@pytest.mark.parametrize("value", ["10000000000000000000000000000", "-0.0001", "NaN", "Infinity"])
def test_out_of_range_money_is_validation_error(value: str) -> None:
    with pytest.raises(CoreError) as failure:
        amount(Decimal(value))
    assert failure.value.code == ErrorCode.VALIDATION_ERROR


def test_valid_money_retains_four_digit_boundary_and_half_up_rounding() -> None:
    assert amount(Decimal("99999999999999.9999")) == Decimal("99999999999999.9999")
    assert amount(Decimal("1.23455")) == Decimal("1.2346")
