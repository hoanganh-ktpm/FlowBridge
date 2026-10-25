# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator, FormatChecker
from workspace_app.decision_api import ExplainDecision
from workspace_app.decision_presentation import presentation, resolve_intent
from workspace_app.decision_service import DecisionPolicy, DecisionService

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / "contracts/portal/schemas/run-result.schema.json").read_text())


def case():
    def side(shortage):
        return {
            "series": [
                {
                    "day": "2026-10-26",
                    "daily_peak_supply_shortage": shortage,
                    "physical_inventory": 10,
                }
            ],
            "metrics": {
                "peak_shortage": 0,
                "peak_supply_shortage": shortage,
                "forecast_demand": 20,
                "known_orders_at_risk": 1,
            },
            "orders": [
                {
                    "order_id": "order-1",
                    "lines": [{"sku": "A", "uom": "EA", "unallocated_quantity": 30}],
                    "shortages_at_deadline": [],
                }
            ],
        }

    return {
        "id": str(uuid4()),
        "title": "Chuẩn bị SO-001",
        "status": "APPROVED",
        "warehouse_id": str(uuid4()),
        "order_id": "order-1",
        "workflow_instance_ref": None,
        "revision": {
            "id": str(uuid4()),
            "number": 1,
            "payload": {
                "scope": {"sku": "A"},
                "scenario": {
                    "baseline": side(30),
                    "proposed": side(0),
                    "reference": side(30),
                    "evidence": {
                        "metric_semantics_version": "physical_supply_deficit_v1",
                        "unit": "EA",
                        "snapshot_at": "2026-10-26T08:00:00+07:00",
                    },
                },
            },
        },
    }


@pytest.mark.parametrize("intent", ["evidence", "comparison", "tasks", "outcome", None])
def test_presentation_reuses_canonical_schema_and_references(intent):
    result = presentation(case(), intent)
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(result)
    datasets = {d["id"]: d for d in result["datasets"]}
    sources = {s["id"] for s in result["sources"]}
    for dataset in datasets.values():
        fields = {c["field"] for c in dataset["columns"]}
        assert set(dataset["source_ids"]) <= sources
        assert all(set(row) == fields for row in dataset["rows"])
    for chart in result["charts"]:
        columns = {c["field"]: c for c in datasets[chart["dataset_id"]]["columns"]}
        assert chart["x_field"] in columns
        assert all(columns[s["field"]]["unit"] == chart["y_unit"] for s in chart["series"])
    if intent in {"evidence", "comparison"}:
        # Physical event-time shortage, not the zero day-end shortage field.
        assert datasets["scenario-supply"]["rows"][0]["peak_shortage"] == 30
        assert datasets["scenario-supply"]["context"]["as_of"] == "2026-10-26T08:00:00+07:00"
        assert result["charts"][0]["stacking"] == "none"
    elif intent == "outcome":
        assert "chưa xác minh" in result["answer"]
        assert not result["charts"]
    elif intent is None:
        assert result["status"] == "needs_input" and len(result["questions"][0]["options"]) == 4


def test_natural_language_is_bounded_to_read_tools_and_does_not_interpret_code():
    assert resolve_intent("Hồ sơ đang chờ ai?") == "tasks"
    assert resolve_intent("So sánh trước và sau") == "comparison"
    assert resolve_intent("Đã bàn giao chưa?") == "outcome"
    for question in ("duyệt ngay", "xóa dữ liệu", "execute SQL", "đổi tiền tệ", ""):
        assert resolve_intent(question) is None
    old = case()
    del old["revision"]["payload"]["scenario"]["evidence"]["metric_semantics_version"]
    result = presentation(old, "comparison")
    assert result["datasets"] == [] and result["charts"] == []
    assert any(w["code"] == "RECOMPUTE_REQUIRED" for w in result["warnings"])


@pytest.mark.asyncio
async def test_explanations_require_current_case_grants_and_matching_revision():
    service = DecisionService(None, None, DecisionPolicy({}), AsyncMock())
    record = case()
    body = ExplainDecision(revision_id=record["revision"]["id"], intent="comparison")
    service.get = AsyncMock(return_value=record)
    assert (await service.explain(None, uuid4(), body))["datasets"]
    with pytest.raises(HTTPException) as stale:
        await service.explain(None, uuid4(), body.model_copy(update={"revision_id": uuid4()}))
    assert stale.value.status_code == 409
    service.get.side_effect = HTTPException(403, "Source grant revoked")
    with pytest.raises(HTTPException) as denied:
        await service.explain(None, uuid4(), body)
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_my_tasks_only_contains_mine_and_warns_about_unconfirmed_sources():
    service = DecisionService(None, None, DecisionPolicy({}), AsyncMock(), workflow=object())
    records = [
        dict(id=str(uuid4()), status="PENDING_APPROVAL", title=title)
        for title in ("Có nhiệm vụ", "Nguồn lỗi")
    ]
    service.list = AsyncMock(return_value={"items": records, "warnings": [], "has_more": False})

    async def state(ctx, identifier):
        if str(identifier) == records[1]["id"]:
            raise HTTPException(503, "Engine unavailable")
        return {
            "case": {"revision": {"id": str(uuid4())}},
            "workflow": {"tasks": [{"id": "mine", "mine": True}, {"id": "other", "mine": False}]},
        }

    service.workflow_state = state
    result = await service.tasks(SimpleNamespace())
    assert [r["task"]["id"] for r in result["items"]] == ["mine"]
    assert result["warnings"]
