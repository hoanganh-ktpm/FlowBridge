# SPDX-License-Identifier: GPL-3.0-or-later
"""Catalog envelope recovery retains trust, pagination and service failure boundaries."""

import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.tools import tool_result  # noqa: E402
from flowbridge_agent.ver2_coordination import (  # noqa: E402
    Request,
    collect,
    finish_reply,
    invocation,
)


def receipt(*, total=6, page_size=10, truncated=False):
    return tool_result(
        "catalog_list",
        {"resource": "suppliers", "query": None, "page": 1, "page_size": page_size},
        {
            "status": "partial" if truncated else "success" if total else "not_found",
            "resource": "suppliers",
            "items": [
                {
                    "supplier_id": f"00000000-0000-4000-8000-{i:012}",
                    "supplier_code": f"NCC-{i:02}",
                    "supplier_name": f"Nhà cung cấp {i}",
                    "status": "active",
                }
                for i in range(1, min(total, page_size) + 1)
            ],
            "page": 1,
            "page_size": page_size,
            "total_matches": None if truncated else total,
            "matched_count_at_least": total,
            "has_more": total > page_size,
            "next_page": 2 if total > page_size else None,
            "truncated": truncated,
            "warnings": ["Filtered catalog scan incomplete"] if truncated else [],
        },
    )


def request():
    return Request(
        task="list_catalog",
        question="các nhà cung cấp hiện tại",
        business_date="2026-10-19",
        known={"resource": "suppliers", "query": None, "page": None, "page_size": None},
    )


@pytest.mark.parametrize("raw", [{}, {"service": "database", "resolved": []}, {"text": "six"}])
def test_invalid_model_envelope_uses_six_actual_suppliers_without_selecting_one(raw):
    with invocation("database") as scope:
        scope.request = request()
        actual = receipt()
        collect(scope, actual)
        output = finish_reply(raw, scope)
        assert output["status"] == "success"
        assert output["warnings"] == []
        assert output["results"] == [actual]
        assert output["resolved"] == {}
        assert output["trace"]["request_id"] == scope.run.identifier
        assert scope.run.events[-1]["recovered_by"] == "VERIFIED_CATALOG_RECEIPT"
        assert scope.run.model_calls == scope.run.a2a_calls == 0


@pytest.mark.parametrize("truncated", [False, True])
def test_more_pages_or_incomplete_scans_stay_partial(truncated):
    with invocation("database") as scope:
        scope.request = request()
        collect(scope, receipt(total=24, truncated=truncated))
        output = finish_reply({}, scope)
        assert output["status"] == "partial"
        assert "CATALOG_PAGE_HAS_MORE" in output["warnings"]
        if truncated:
            assert "Filtered catalog scan incomplete" in output["warnings"]
        assert len(output["results"][0]["data"]["items"]) == 10


def test_empty_verified_catalog_stays_not_found():
    with invocation("database") as scope:
        scope.request = request()
        collect(scope, receipt(total=0))
        assert finish_reply({}, scope)["status"] == "not_found"


@pytest.mark.parametrize("role", ["prediction", "knowledge"])
def test_recovery_does_not_change_ml_or_rag(role):
    with invocation(role) as scope:
        scope.request = request()
        collect(scope, receipt())
        output = finish_reply({}, scope)
        assert output["status"] == "error"
        assert "SERVICE_OUTPUT_INVALID" in output["warnings"]


@pytest.mark.parametrize(
    "case", ["unobserved", "resource", "query", "budget", "service", "extra_task"]
)
def test_missing_proof_wrong_request_or_execution_failure_is_not_promoted(case):
    with invocation("database") as scope:
        scope.request = request()
        actual = receipt()
        raw = {}
        if case == "unobserved":
            scope.receipts = [actual]
        else:
            collect(scope, actual)
        if case == "resource":
            scope.request.known["resource"] = "products"
        if case == "query":
            scope.request.known["query"] = "NCC-03"
        if case == "budget":
            scope.blocked.append("MODEL_BUDGET_EXHAUSTED")
        if case == "service":
            raw = {"service": "prediction", "status": "success"}
        if case == "extra_task":
            scope.request.task = "offers"
        output = finish_reply(raw, scope)
        assert output["status"] == "error"
        assert "SERVICE_OUTPUT_INVALID" in output["warnings"]


@pytest.mark.parametrize("case", ["uuid", "total", "next_page", "status", "row_count"])
def test_inconsistent_backend_data_is_not_promoted(case):
    with invocation("database") as scope:
        scope.request = request()
        actual = receipt()
        data = actual["data"]
        if case == "uuid":
            data["items"][0]["supplier_id"] = "invented-id"
        if case == "total":
            data["total_matches"] = 99
        if case == "next_page":
            data["next_page"] = 2
        if case == "status":
            actual["status"] = "not_found"
        if case == "row_count":
            data["items"].pop()
        collect(scope, actual)
        output = finish_reply({}, scope)
        assert output["status"] == "error"


def test_http_failure_is_not_replaced_with_catalog_success():
    with invocation("database") as scope:
        scope.request = request()
        failed = tool_result(
            "catalog_list",
            {"resource": "suppliers"},
            {"status": "error", "code": "BACKEND_REQUEST_FAILED", "http_status": 401},
        )
        collect(scope, failed)
        output = finish_reply({}, scope)
        assert output["status"] == "error"
        assert output["results"][0]["error"]["http_status"] == 401


def test_valid_model_envelope_with_forged_rows_still_fails():
    with invocation("database") as scope:
        scope.request = request()
        actual = receipt()
        collect(scope, actual)
        forged = deepcopy(actual)
        forged["data"]["items"][0]["supplier_name"] = "Invented name"
        output = finish_reply(
            {"service": "database", "status": "success", "results": [forged]}, scope
        )
        assert output["status"] == "error"
        assert "UNSUPPORTED_TOOL_RESULT" in output["warnings"]
        assert output["results"] == [actual]
