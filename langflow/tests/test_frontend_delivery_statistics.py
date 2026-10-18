# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent import frontend_delivery_statistics as module  # noqa: E402
from flowbridge_agent.config import BackendConfig  # noqa: E402
from flowbridge_agent.frontend_response_builder import build_response  # noqa: E402
from flowbridge_agent.frontend_response_contract import validate_response  # noqa: E402

from flowbridge.supplier_assistant.delivery_statistics import DeliveryStatistics  # noqa: E402

SUPPLIER = "00000000-0000-4000-8000-000000000003"
JOB = "00000000-0000-4000-8000-000000000001"


def statistics():
    return DeliveryStatistics(
        supplier_id=UUID(SUPPLIER),
        supplier_code="NCC-03",
        supplier_name="Nhà cung cấp 03",
        as_of=datetime(2026, 9, 30, 10, tzinfo=UTC),
        issued_from=date(2026, 9, 1),
        issued_to=date(2026, 9, 30),
        total_eligible_orders=188,
        completed_orders=181,
        late_rate_percent=60.77,
        counts={
            "completed_on_time": 71,
            "completed_late": 110,
            "open_overdue": 4,
            "open_not_due": 3,
            "unknown_schedule": 0,
            "partial_open": 3,
            "excluded_cancelled": 2,
        },
        metric_definition="Theo lịch giao gốc.",
        denominator_definition="Đơn đã nhận đủ.",
        complete=True,
        warnings=[],
    )


def receipts():
    return [
        {
            "tool": "knowledge_search_supplier",
            "status": "success",
            "data": {"chunks": [{"reference": {"supplier_id": SUPPLIER}}]},
        }
    ]


def test_database_counts_have_chart_and_summary_with_explicit_denominator_and_no_double_count():
    response = module.append_statistics(build_response("Nhận xét từ tài liệu", []), statistics())
    validate_response(response)
    counts, summary = response["datasets"]
    assert sum(r["order_count"] for r in counts["rows"]) == 188
    assert len(counts["rows"]) == 5  # Partial-open and cancelled are not additive pie/bar groups.
    assert summary["rows"][0]["late_rate_percent"] == 60.77
    assert summary["rows"][0]["completed_orders"] == 181
    assert summary["rows"][0]["partially_received_open_orders"] == 3
    assert response["charts"][0]["dataset_id"] == counts["id"]
    assert counts["source_ids"] == [response["sources"][0]["id"]]
    assert counts["context"]["as_of"] and counts["context"]["denominator_definition"]
    assert "NO_STRUCTURED_DATA" not in {w["code"] for w in response["warnings"]}


def test_no_completed_orders_keeps_rate_null_and_does_not_invent_zero_percent():
    data = statistics().model_dump()
    data.update(total_eligible_orders=4, completed_orders=0, late_rate_percent=None)
    data["counts"].update(completed_on_time=0, completed_late=0, open_not_due=0)
    response = module.append_statistics(build_response("", []), DeliveryStatistics(**data))
    assert response["datasets"][1]["rows"][0]["late_rate_percent"] is None


def test_supplier_selection_uses_tool_receipts_never_answer_or_arbitrary_model_fields():
    assert module.supplier_ids([]) == []
    assert module.supplier_ids([{"tool": "catalog_list", "data": {"supplier_id": SUPPLIER}}]) == []
    assert module.supplier_ids(receipts()) == [SUPPLIER]
    assert (
        module.supplier_ids([{"tool": "knowledge_search_supplier", "data": {"chunks": [42]}}]) == []
    )


@pytest.mark.parametrize("outcome", ["success", "wrong_supplier", "bad_totals", "network_error"])
def test_http_enrichment_uses_real_service_context_and_retains_data_on_failure(outcome):
    config = BackendConfig(
        origin="http://127.0.0.1:18083", ai_token="unit-test-credential", job_context=JOB
    )
    requested = []

    def handle(request):
        requested.append(request)
        assert request.headers["Authorization"] == "Bearer unit-test-credential"
        assert request.headers["X-Job-Context"] == JOB
        assert json.loads(request.content) == {"supplier_id": SUPPLIER}
        if outcome == "network_error":
            raise httpx.ConnectError("offline")
        data = statistics().model_dump(mode="json")
        if outcome == "wrong_supplier":
            data["supplier_id"] = JOB
        if outcome == "bad_totals":
            data["late_rate_percent"] = 0
        return httpx.Response(200, json=data)

    original = httpx.AsyncClient
    with (
        patch.object(module.BackendConfig, "from_local", return_value=config),
        patch.object(
            module.httpx,
            "AsyncClient",
            side_effect=lambda **kw: original(transport=httpx.MockTransport(handle), **kw),
        ),
    ):
        response = asyncio.run(
            module.enrich_delivery_statistics(build_response("RAG", []), receipts())
        )
    assert len(requested) == 1
    if outcome == "success":
        assert response["charts"]
    else:
        assert response["charts"] == [] and response["status"] == "partial"
        assert "DELIVERY_STATISTICS_UNAVAILABLE" in {w["code"] for w in response["warnings"]}
