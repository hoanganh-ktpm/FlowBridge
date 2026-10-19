# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite

from flowbridge.agent_hub.contract import encoded, validate

ACTIVE = {"queued", "running", "cancelling"}
TERMINAL = {"completed", "needs_input", "failed", "cancelled"}
PREFIX = "/api/v1/agent-hub"


def result_url(run_id: str) -> str:
    return f"{PREFIX}/runs/{run_id}"


def empty_result(run_id: str, status: str, error: dict | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "status": status,
        "run_id": run_id,
        "answer": "",
        "datasets": [],
        "tables": [],
        "charts": [],
        "sources": [],
        "warnings": [],
        "questions": [],
        "error": error,
    }


def failed_result(run_id: str, code: str, message: str) -> dict:
    return empty_result(
        run_id,
        "failed",
        {
            "code": code,
            "message": message,
            "retryable": False,
            "trace_id": run_id,
        },
    )


def unique(items: list[dict], field: str = "id") -> dict:
    result = {item[field]: item for item in items}
    if len(result) != len(items):
        raise ValueError("DUPLICATE_IDENTIFIER")
    return result


def validate_result(result: dict) -> None:
    validate(result, "RunResult")
    if len(encoded(result)) > 2_097_152 or sum(len(d["rows"]) for d in result["datasets"]) > 5000:
        raise ValueError("RESULT_TOO_LARGE")
    datasets, sources = unique(result["datasets"]), unique(result["sources"])
    for group in ("tables", "charts", "questions"):
        unique(result[group])
    for dataset in datasets.values():
        columns = unique(dataset["columns"], "field")
        if not set(dataset["source_ids"]) <= sources.keys():
            raise ValueError("UNKNOWN_SOURCE")
        context = dataset["context"]
        if context.get("period_from", "") > context.get("period_to", ""):
            raise ValueError("INVALID_PERIOD")
        for row in dataset["rows"]:
            if row.keys() != columns.keys():
                raise ValueError("ROW_COLUMN_MISMATCH")
            for field, value in row.items():
                column = columns[field]
                if value is None:
                    if not column.get("nullable", False):
                        raise ValueError("NULL_NOT_ALLOWED")
                    continue
                kind = column["type"]
                valid = {
                    "string": isinstance(value, str),
                    "decimal": isinstance(value, str),
                    "integer": type(value) is int,
                    "number": type(value) in {int, float},
                    "boolean": type(value) is bool,
                    "date": isinstance(value, str),
                    "datetime": isinstance(value, str),
                }[kind]
                if not valid or (isinstance(value, float) and not isfinite(value)):
                    raise ValueError("INVALID_CELL_TYPE")
                if kind == "decimal":
                    import re

                    scale = column["scale"]
                    pattern = r"-?(?:0|[1-9][0-9]*)" + (rf"\.[0-9]{{{scale}}}" if scale else "")
                    if not re.fullmatch(pattern, value):
                        raise ValueError("INVALID_DECIMAL")
                if kind == "date":
                    date.fromisoformat(value)
                if kind == "datetime" and datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError("TIMEZONE_REQUIRED")
    for table in result["tables"]:
        fields = {c["field"] for c in datasets[table["dataset_id"]]["columns"]}
        if not set(table["fields"]) <= fields:
            raise ValueError("UNKNOWN_TABLE_FIELD")
    for chart in result["charts"]:
        dataset = datasets[chart["dataset_id"]]
        columns = unique(dataset["columns"], "field")
        series = unique(chart["series"], "field")
        labels = [r[chart["x_field"]] for r in dataset["rows"]]
        if any(x is None for x in labels) or len(set(labels)) != len(labels):
            raise ValueError("INVALID_CHART_LABELS")
        for field in series:
            col = columns[field]
            if col["type"] not in {"integer", "number", "decimal"}:
                raise ValueError("NON_NUMERIC_SERIES")
            if chart["stacking"] != "percent" and col.get("unit") != chart["y_unit"]:
                raise ValueError("INCOMPATIBLE_UNITS")
            for row in dataset["rows"]:
                if row[field] is None:
                    raise ValueError("NULL_CHART_VALUE")
                number = Decimal(str(row[field]))
                if not number.is_finite():
                    raise ValueError("INVALID_CHART_VALUE")
                if chart["type"] in {"pie", "donut"} or chart["stacking"] == "percent":
                    if number < 0:
                        raise ValueError("NEGATIVE_COUNT")
        if chart["stacking"] == "percent":
            context = dataset["context"]
            if (
                not all(
                    context.get(k)
                    for k in ("as_of", "count_unit", "metric_definition", "denominator_definition")
                )
                or not context["complete"]
            ):
                raise ValueError("INCOMPLETE_PERCENT_CONTEXT")
            for field in series:
                if columns[field].get("unit") != context["count_unit"]:
                    raise ValueError("PERCENT_REQUIRES_RAW_COUNTS")
            if any(sum(Decimal(str(r[f])) for f in series) <= 0 for r in dataset["rows"]):
                raise ValueError("ZERO_DENOMINATOR")
    for warning in result["warnings"]:
        if warning.get("dataset_id") and warning["dataset_id"] not in datasets:
            raise ValueError("UNKNOWN_WARNING_DATASET")


def from_langflow(raw: dict, run_id: str) -> dict:
    # Input is the authenticated bridge's validated FrontendResponse, never answer prose.
    result = deepcopy(raw)
    result["run_id"] = run_id
    if result["status"] == "failed":
        return failed_result(run_id, "LANGFLOW_EXECUTION_FAILED", "Langflow chưa hoàn tất yêu cầu.")
    if result["status"] == "partial":
        result["status"] = "completed"
        result["warnings"].append({"code": "PARTIAL_RESULT", "message": "Kết quả chưa đầy đủ."})
    result["error"] = None
    result["questions"] = [
        {
            "id": f"q_{i + 1}",
            "field": f"clarification_{i + 1}",
            "prompt": question,
            "kind": "text",
            "required": True,
        }
        for i, question in enumerate(result["questions"])
    ]
    if result["questions"]:
        result["status"] = "needs_input"
    result["warnings"] = [{k: v for k, v in w.items() if v is not None} for w in result["warnings"]]
    for source in result["sources"]:
        if source["kind"] == "document":
            projected = {
                "id": source["id"],
                "kind": "document",
                "document_id": source["document_id"],
                "version": str(source["document_version"]),
                "title": source.get("title") or source["document_id"],
            }
            if source.get("chunk_id"):
                projected["chunk_id"] = source["chunk_id"]
        else:
            projected = {k: source[k] for k in ("id", "kind", "tool")}
        source.clear()
        source.update(projected)
    for dataset in result["datasets"]:
        original = dataset["context"]
        dataset["context"] = {
            k: v
            for k, v in original.items()
            if v is not None
            and k
            in {
                "as_of",
                "complete",
                "period_from",
                "period_to",
                "count_unit",
                "metric_definition",
                "denominator_definition",
                "timezone",
            }
        }
        context = dataset["context"]
        context.setdefault("as_of", None)
        context["filters"] = {
            k: original[k]
            for k in ("product_id", "unit", "currency")
            if original.get(k) is not None
        }
        if context["as_of"] is None:
            result["warnings"].append(
                {
                    "code": "AS_OF_UNKNOWN",
                    "dataset_id": dataset["id"],
                    "message": "Nguồn chưa cung cấp mốc chốt dữ liệu.",
                }
            )
        for column in dataset["columns"]:
            if column.get("unit") is None:
                column.pop("unit", None)
            if column["type"] == "decimal":
                values = [
                    Decimal(row[column["field"]])
                    for row in dataset["rows"]
                    if row[column["field"]] is not None
                ]
                if any(not value.is_finite() for value in values):
                    raise InvalidOperation
                scale = max([0, *[-v.as_tuple().exponent for v in values]])
                if scale > 12:
                    raise ValueError("DECIMAL_SCALE_EXCEEDED")
                column["scale"] = scale
                for row in dataset["rows"]:
                    if row[column["field"]] is not None:
                        row[column["field"]] = format(Decimal(row[column["field"]]), f".{scale}f")
    for chart in result["charts"]:
        if chart["stacking"] == "percent":
            # Legacy graph already computed percentages; never normalize them a second time.
            chart["stacking"] = "normal"
    validate_result(result)
    return result
