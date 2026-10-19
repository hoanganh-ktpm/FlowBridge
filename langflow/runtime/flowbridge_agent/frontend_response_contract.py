# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned, library-independent frontend data contract. No model or I/O."""

import math
import re
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Scalar = str | int | float | bool | None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Warning(Strict):
    code: str
    message: str
    dataset_id: str | None = None


class Source(Strict):
    id: str
    kind: Literal["tool", "document"]
    tool: str
    document_id: str | None = None
    document_version: str | None = None
    chunk_id: str | None = None
    source_uri: str | None = None
    checksum: str | None = None

    @model_validator(mode="after")
    def citation(self):
        if self.kind == "document":
            try:
                UUID(self.document_id)
                UUID(self.chunk_id)
                if not re.fullmatch(r"[a-f0-9]{64}", self.checksum or ""):
                    raise ValueError
                prefix = (
                    "core-document"
                    if (self.source_uri or "").startswith("core-document://")
                    else "local-document"
                )
                if not (self.source_uri or "").startswith(
                    f"{prefix}://{self.document_id}/{self.document_version}#"
                ):
                    raise ValueError
                match = re.fullmatch(
                    r"(?:core|local)-document://[^\s?#]+#(?:bytes|chars)=(\d+):(\d+)",
                    self.source_uri or "",
                )
                if not match or int(match[1]) >= int(match[2]):
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise ValueError("INVALID_SOURCE_REFERENCE") from None
        return self


class Column(Strict):
    field: str
    label: str
    type: Literal["string", "integer", "number", "decimal", "boolean", "date"]
    unit: str | None = None
    nullable: bool = True


class Context(Strict):
    product_id: str | None = None
    unit: str | None = None
    currency: str | None = None
    period_from: str | None = None
    period_to: str | None = None
    as_of: str | None = None
    complete: bool = False
    metric_definition: str | None = None
    count_unit: str | None = None
    denominator_definition: str | None = None
    scope: str | None = None
    page: int | None = None
    total_matches: int | None = None
    next_page: int | None = None
    history_reference: dict[str, Scalar] | None = None
    reference_matches: bool | None = None
    expected_count_matches: bool | None = None


class Dataset(Strict):
    id: str
    title: str
    columns: list[Column] = Field(max_length=40)
    rows: list[dict[str, Scalar]] = Field(max_length=200)
    context: Context
    source_ids: list[str]

    @model_validator(mode="after")
    def cells(self):
        fields = {c.field: c for c in self.columns}
        if not fields or len(fields) != len(self.columns):
            raise ValueError("INVALID_DATASET_COLUMNS")
        for row in self.rows:
            if set(row) != fields.keys():
                raise ValueError("ROW_COLUMNS_MISMATCH")
            for key, value in row.items():
                col = fields[key]
                if value is None:
                    if not col.nullable:
                        raise ValueError("NULL_NOT_ALLOWED")
                    continue
                valid = {
                    "string": isinstance(value, str),
                    "date": isinstance(value, str)
                    and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)),
                    "integer": type(value) is int,
                    "number": type(value) in (float, int) and math.isfinite(value),
                    "decimal": isinstance(value, str)
                    and bool(re.fullmatch(r"-?\d+(?:\.\d+)?", value)),
                    "boolean": type(value) is bool,
                }[col.type]
                if not valid:
                    raise ValueError("INVALID_CELL_TYPE")
                if col.type == "date":
                    date.fromisoformat(value)
        return self


class Table(Strict):
    id: str
    title: str
    dataset_id: str
    fields: list[str]


class Series(Strict):
    field: str
    label: str


class Chart(Strict):
    id: str
    title: str
    dataset_id: str
    type: Literal["bar", "line", "pie"]
    orientation: Literal["vertical", "horizontal"] = "vertical"
    stacking: Literal["none", "normal", "percent"] = "none"
    x_field: str
    x_label: str
    y_label: str
    y_unit: str
    series: list[Series] = Field(min_length=1, max_length=8)


class Failure(Strict):
    code: str
    message: str
    retryable: bool = False


class FrontendResponse(Strict):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["completed", "partial", "needs_input", "failed"]
    run_id: str | None = None
    answer: str = Field(max_length=24000)
    datasets: list[Dataset] = Field(max_length=30)
    tables: list[Table] = Field(max_length=30)
    charts: list[Chart] = Field(max_length=30)
    sources: list[Source] = Field(max_length=250)
    warnings: list[Warning] = Field(max_length=100)
    questions: list[str] = Field(default_factory=list, max_length=20)
    error: Failure | None = None

    @model_validator(mode="after")
    def references(self):
        datasets = {d.id: d for d in self.datasets}
        sources = {s.id for s in self.sources}
        for values in (self.datasets, self.tables, self.charts, self.sources):
            if len({v.id for v in values}) != len(values):
                raise ValueError("DUPLICATE_ID")
        for d in self.datasets:
            if not set(d.source_ids) <= sources:
                raise ValueError("UNKNOWN_SOURCE")
        if any(w.dataset_id is not None and w.dataset_id not in datasets for w in self.warnings):
            raise ValueError("UNKNOWN_WARNING_DATASET")
        for view in [*self.tables, *self.charts]:
            d = datasets.get(view.dataset_id)
            if d is None:
                raise ValueError("UNKNOWN_DATASET")
            columns = {c.field: c for c in d.columns}
            if isinstance(view, Table):
                if not view.fields or not set(view.fields) <= columns.keys():
                    raise ValueError("UNKNOWN_TABLE_FIELD")
                continue
            if view.x_field not in columns or not d.rows:
                raise ValueError("INVALID_CHART_AXIS")
            units = set()
            for series in view.series:
                col = columns.get(series.field)
                if col is None or col.type not in {"integer", "number", "decimal"}:
                    raise ValueError("INVALID_SERIES")
                units.add(col.unit)
                if any(r[series.field] is None for r in d.rows):
                    raise ValueError("NULL_SERIES")
            if len(units) != 1 or units != {view.y_unit}:
                raise ValueError("CHART_UNIT_MISMATCH")
            labels = [r[view.x_field] for r in d.rows]
            if None in labels or len(set(labels)) != len(labels):
                raise ValueError("AMBIGUOUS_CHART_LABELS")
            if view.type != "bar" and view.stacking != "none":
                raise ValueError("STACKING_REQUIRES_BAR")
            if view.type == "pie" and len(view.series) != 1:
                raise ValueError("PIE_REQUIRES_ONE_SERIES")
            if view.stacking == "percent":
                if view.y_unit != "%" or not all(
                    (
                        d.context.metric_definition,
                        d.context.count_unit,
                        d.context.denominator_definition,
                        d.context.period_from,
                        d.context.period_to,
                        d.context.as_of,
                    )
                ):
                    raise ValueError("PERCENT_DEFINITION_REQUIRED")
                for row in d.rows:
                    numbers = [Decimal(str(row[s.field])) for s in view.series]
                    if any(n < 0 for n in numbers) or abs(sum(numbers) - 100) > Decimal("0.01"):
                        raise ValueError("PERCENT_TOTAL_MISMATCH")
        if self.status == "failed" and self.error is None:
            raise ValueError("FAILURE_CODE_REQUIRED")
        if self.status == "completed" and self.error is not None:
            raise ValueError("COMPLETED_WITH_ERROR")
        return self


def validate_response(value: dict) -> dict:
    return FrontendResponse.model_validate(value).model_dump(mode="json")
