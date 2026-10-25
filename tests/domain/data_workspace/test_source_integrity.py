# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy

import pytest
from fastapi import HTTPException
from workspace_app.source_integrity import validate_planning_facts


def facts():
    return (
        [
            {
                "warehouse_id": "w",
                "sku": "K120",
                "uom": "EA",
                "on_hand_quantity": 100,
                "reserved_quantity": 80,
                "available_quantity": 20,
            }
        ],
        [{"warehouse_id": "w", "sku": "K120", "uom": "EA", "status": "APPROVED"}],
        [
            {
                "warehouse_id": "w",
                "sku": "K120",
                "uom": "EA",
                "order_id": "O",
                "order_line_id": "L",
                "status": "CONFIRMED",
            }
        ],
        [],
    )


def test_consistent_identity_is_read_only_and_keeps_other_warehouse_separate():
    stock, inbound, orders, commitments = facts()
    stock.append({**stock[0], "warehouse_id": "other", "uom": "BOX"})
    before = deepcopy((stock, inbound, orders, commitments))
    assert validate_planning_facts(stock, inbound, orders, commitments, "w") == {"K120": "EA"}
    assert (stock, inbound, orders, commitments) == before


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda s, i, o, c: o[0].update(sku="UNKNOWN"), "MISSING_STOCK_COVERAGE"),
        (lambda s, i, o, c: i[0].update(uom="BOX"), "SOURCE_UOM_MISMATCH"),
        (lambda s, i, o, c: s[0].update(available_quantity=100), "STOCK_BALANCE_INVALID"),
        (lambda s, i, o, c: s.append(dict(s[0])), "STOCK_GRAIN_INVALID"),
        (lambda s, i, o, c: o.append(dict(o[0])), "ORDER_GRAIN_INVALID"),
        (lambda s, i, o, c: c.append({**o[0], "sku": "WRONG"}), "COMMITMENT_IDENTITY_MISMATCH"),
    ],
)
def test_cross_source_mismatch_fails_closed(change, code):
    values = facts()
    change(*values)
    with pytest.raises(HTTPException) as error:
        validate_planning_facts(*values, "w")
    assert error.value.status_code == 422
    assert error.value.detail["code"] == code


def test_zero_is_only_zero_when_there_is_a_verified_stock_row():
    stock, inbound, orders, commitments = facts()
    stock[0].update(on_hand_quantity=0, reserved_quantity=0, available_quantity=0)
    assert validate_planning_facts(stock, inbound, orders, commitments, "w") == {"K120": "EA"}
    with pytest.raises(HTTPException) as error:
        validate_planning_facts([], inbound, orders, commitments, "w")
    assert error.value.detail["code"] == "MISSING_STOCK_COVERAGE"


@pytest.mark.asyncio
async def test_each_source_keeps_its_own_read_window_and_hash_without_claiming_atomic_snapshot():
    from datetime import datetime

    from .test_decision_workspace import PlanningBoundary, call

    boundary = PlanningBoundary()
    result = await call(boundary)
    assert result.status_code == 200
    sources = result.json()["evidence"]["sources"]
    assert len(sources) == 9
    for source in sources:
        assert datetime.fromisoformat(source["read_completed_at"]) >= datetime.fromisoformat(
            source["read_started_at"]
        )
        assert source["rows"] == len(boundary.facts[source["ref"]["data_product_id"]])
        assert len(source["input_sha256"]) == 64
        assert source["ref"]["contract_version_id"]
