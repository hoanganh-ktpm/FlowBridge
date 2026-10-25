# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from workspace_app.decision_events import validate_event
from workspace_app.decision_impact import VERSION, assess_change

from app.platform.business_operations import canonical


def proof():
    return {
        "input_sha256": "a" * 64,
        "warehouse_confirmable": True,
        "logistics_confirmable": True,
        "impact_evidence": {
            "version": VERSION,
            "contract": {
                "original_order": [{"order_line_id": "L1", "ordered_quantity": 30}],
                "plan": {"at_risk": False, "already_overdue": False},
                "forecast_history": "b" * 64,
                "schedule": [
                    {"carrier_code": "GHN", "scheduled_handoff_at": "2026-10-26T09:00:00+07:00"}
                ],
                "preparation_minutes": 120,
            },
            "dependencies": {"stock": "c" * 64, "capacity": "d" * 64},
        },
    }


def test_unrelated_or_metadata_changes_do_not_reset_a_still_identical_obligation():
    base = proof()
    assert assess_change(base, deepcopy(base))["classification"] == "UNCHANGED"
    now = deepcopy(base)
    now["input_sha256"] = "e" * 64
    now["impact_evidence"]["dependencies"]["stock"] = "f" * 64
    result = assess_change(base, now)
    assert result["classification"] == "NO_EFFECT" and not result["requires_revision"]
    assert result["observed_changes"][0]["dependency"] == "stock"


@pytest.mark.parametrize("change", ["line", "history", "slot", "preparation", "broken", "expired"])
def test_changed_obligations_or_conditions_require_revision_even_without_an_event(change):
    base, now = proof(), proof()
    contract = now["impact_evidence"]["contract"]
    if change == "line":
        contract["original_order"][0]["ordered_quantity"] = 31
    elif change == "history":
        contract["forecast_history"] = "f" * 64
    elif change == "slot":
        contract["schedule"][0]["carrier_code"] = "VTP"
    elif change == "preparation":
        contract["preparation_minutes"] = 0
    elif change == "broken":
        now["logistics_confirmable"] = False
    else:
        # No raw input changed, but the clock has passed the physical cutoff.
        contract["plan"]["already_overdue"] = True
    assert assess_change(base, now)["requires_revision"] is True


def test_old_evidence_requires_recompute_instead_of_silent_upgrade():
    base = proof()
    del base["impact_evidence"]
    assert assess_change(base, proof())["requires_revision"] is True


def event():
    raw = dict(
        id=str(uuid4()),
        org_id=str(uuid4()),
        warehouse_id=str(uuid4()),
        producer="warehouse",
        event_type="warehouse.receipt_posted",
        schema_version=1,
        aggregate_type="goods_receipt",
        aggregate_id=str(uuid4()),
        aggregate_version=1,
        correlation_id=str(uuid4()),
        occurred_at=datetime.now(UTC).isoformat(),
        source_payload_hash="a" * 64,
    )
    raw["summary_hash"] = canonical(raw)
    return raw


def test_owner_event_hash_scope_and_producer_are_verified():
    from uuid import UUID

    raw = event()
    args = "warehouse", UUID(raw["org_id"]), UUID(raw["warehouse_id"])
    assert validate_event(raw, *args) == raw
    for changed in (
        {"aggregate_version": 2},
        {"warehouse_id": str(uuid4())},
        {"producer": "sales"},
    ):
        with pytest.raises(HTTPException):
            validate_event({**raw, **changed}, *args)


@pytest.mark.asyncio
async def test_event_sync_accepts_only_owner_reads_and_does_not_approve_or_revalidate():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import UUID

    from workspace_app.decision_service import DecisionPolicy, DecisionService

    raw = event()
    ctx = SimpleNamespace(org_id=UUID(raw["org_id"]), owner_id=uuid4())
    case = {
        "id": str(uuid4()),
        "warehouse_id": raw["warehouse_id"],
        "revision": {"id": str(uuid4())},
        "created_at": datetime.now(UTC).isoformat(),
    }
    repo = SimpleNamespace(observe_source=AsyncMock(return_value=True))

    async def read(ctx, producer, body):
        assert set(body) == {"org_id", "warehouse_id", "since", "limit"}
        return {"items": [raw] if producer == "warehouse" else [], "has_more": False}

    workflow, inspector = AsyncMock(), AsyncMock()
    service = DecisionService(
        repo, None, DecisionPolicy({}), AsyncMock(), inspector, workflow, read
    )
    service.get = AsyncMock(return_value=case)
    result = await service.sync_events(ctx, UUID(case["id"]))
    assert result["new_observations"] == 1 and result["requires_source_revalidation"] is True
    assert repo.observe_source.await_count == 1
    assert not workflow.mock_calls and not inspector.mock_calls
    raw["warehouse_id"] = str(uuid4())
    with pytest.raises(HTTPException) as mixed_scope:
        await service.sync_events(ctx, UUID(case["id"]))
    assert mixed_scope.value.status_code == 502
    assert repo.observe_source.await_count == 1


@pytest.mark.asyncio
async def test_revoked_native_event_permission_hides_persisted_source_observations():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from workspace_app.decision_service import DecisionPolicy, DecisionService

    raw = event()
    repo = SimpleNamespace(
        timeline=AsyncMock(return_value=[{"event_type": "SOURCE_OBSERVED", "payload": raw}])
    )
    reader = AsyncMock(side_effect=HTTPException(403, "Source grant revoked"))
    service = DecisionService(repo, None, DecisionPolicy({}), AsyncMock(), event_reader=reader)
    service.get = AsyncMock(return_value={"warehouse_id": raw["warehouse_id"]})
    with pytest.raises(HTTPException) as revoked:
        await service.timeline(SimpleNamespace(org_id=uuid4()), uuid4())
    assert revoked.value.status_code == 403
