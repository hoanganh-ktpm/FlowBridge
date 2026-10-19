# SPDX-License-Identifier: GPL-3.0-or-later
import runpy
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from flowbridge.supplier_assistant.delivery_statistics import classify_orders
from flowbridge.supplier_assistant.prediction.features import FeatureBuilder


@pytest_asyncio.fixture
async def fixture_plan(monkeypatch):
    source = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/seed_multi_item_orders.py")
    )
    products = {
        f"DEMO-P{i:02}": {
            "product_id": uuid4(),
            "product_code": f"DEMO-P{i:02}",
            "product_name": f"Product {i}",
            "product_category": "office",
            "unit": "ram" if i == 1 else "cái",
            "status": "active",
        }
        for i in range(1, 7)
    }
    suppliers = {
        f"DEMO-S{i:02}": {
            "supplier_id": uuid4(),
            "supplier_name": f"Supplier {i}",
            "status": "active",
        }
        for i in range(1, 5)
    }
    offers = {
        (s["supplier_id"], p["product_id"]): {
            "current_unit_price": Decimal("100000"),
            "min_order_qty": Decimal(1),
            "is_available": True,
            "currency": "VND",
        }
        for s in suppliers.values()
        for p in products.values()
    }
    masters = (
        products,
        suppliers,
        offers,
        {"department_id": uuid4()},
        {r: uuid4() for r in ["EMPLOYEE", "APPROVER", "WAREHOUSE", "AI_SERVICE", "PO_SERVICE"]},
    )
    build = source["build_plan"]
    monkeypatch.setitem(build.__globals__, "master_data", AsyncMock(return_value=masters))
    monkeypatch.setitem(
        build.__globals__,
        "PredictionRepository",
        lambda repo: SimpleNamespace(facts=AsyncMock(return_value=[])),
    )
    monkeypatch.setitem(
        build.__globals__,
        "FeatureBuilder",
        lambda: FeatureBuilder(
            quantity_references={
                str(p["product_id"]): {"unit": p["unit"], "quantity": "50"}
                for p in products.values()
            }
        ),
    )
    recorded = datetime(2026, 10, 20, 10, tzinfo=UTC)
    rows, preview = await build(None, date(2026, 10, 20), recorded)
    return source, rows, preview, recorded


@pytest.mark.asyncio
async def test_multi_supplier_request_splits_into_supplier_specific_multi_item_orders(fixture_plan):
    source, rows, preview, _ = fixture_plan
    source["validate_plan"](rows)
    assert len(rows["purchase_requests"]) == 3 and len(rows["purchase_orders"]) == 4
    assert len(rows["purchase_order_items"]) == len(rows["purchase_request_items"]) == 9
    request = next(r for r in rows["purchase_requests"] if r["request_code"].endswith("003"))
    split = [o for o in rows["purchase_orders"] if o["request_id"] == request["request_id"]]
    assert len(split) == 2 and len({o["supplier_id"] for o in split}) == 2
    assert len({p["po_number"] for p in preview}) == 4
    for prediction in rows["risk_predictions"]:
        assert prediction["status"] == "snapshot_only" and prediction["risk_score"] is None
        assert prediction["feature_snapshot"]["model_executed"] is False


@pytest.mark.asyncio
async def test_receipts_and_statuses_reproduce_mixed_lateness_and_partial_fulfillment(fixture_plan):
    _, rows, _, recorded = fixture_plan
    details = []
    counts = classify_orders(
        rows["purchase_orders"],
        rows["purchase_order_items"],
        rows["purchase_order_schedules"],
        rows["goods_receipts"],
        recorded,
        details=details,
    )
    assert counts.completed_late == 1 and counts.open_overdue == counts.partial_open == 1
    assert counts.open_not_due == 2
    complete = next(d for d in details if d["po_number"].endswith("001"))
    assert complete["max_days_late"] == 4 and complete["late_schedule_count"] == 2
    assert sorted(s["days_late"] for s in complete["schedules"]) == [0, 3, 4]
    before = classify_orders(
        rows["purchase_orders"],
        rows["purchase_order_items"],
        rows["purchase_order_schedules"],
        rows["goods_receipts"],
        recorded - timedelta(seconds=1),
    )
    assert before.completed_late == before.completed_on_time == 0  # Import not yet known.


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_refuses_partial_or_modified_existing_records(fixture_plan):
    source, rows, _, _ = fixture_plan
    source["verify_existing"](rows, deepcopy(rows))
    partial = deepcopy(rows)
    partial["goods_receipts"].pop()
    with pytest.raises(RuntimeError, match="Partial seed"):
        source["verify_existing"](rows, partial)
    modified = deepcopy(rows)
    modified["purchase_order_items"][0]["ordered_quantity"] += 1
    with pytest.raises(RuntimeError, match="has changed"):
        source["verify_existing"](rows, modified)
    with pytest.raises(AssertionError):
        source["validate_plan"](modified)
