# SPDX-License-Identifier: GPL-3.0-or-later
import re
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=re.compile(r"^(?!__)[a-z_][a-z0-9_]{0,62}$"))]
Number = Annotated[float, Field(allow_inf_nan=False)]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProductRef(DTO):
    data_product_id: UUID
    contract_version_id: UUID


class PlanningOptionsRequest(DTO):
    stock: ProductRef
    catalog: ProductRef | None = None


class AnalysisConfig(ProductRef):
    name: str = Field(min_length=1, max_length=120)
    time_column: Identifier
    entity_column: Identifier
    target_column: Identifier
    unit: str = Field(min_length=1, max_length=24)
    unit_column: Identifier | None = None
    metric_type: Literal["FLOW", "STOCK"] = "FLOW"
    aggregation: Literal["SUM", "COUNT", "AVG", "LAST", "MIN", "MAX"] = "SUM"
    frequency: Literal["D", "W", "M"] = "D"
    timezone: str = "Asia/Ho_Chi_Minh"
    missing_policy: Literal["error", "zero", "forward_fill"] = "error"
    duplicate_policy: Literal["error", "aggregate"] = "error"
    start: date
    end: date
    entities: list[str] = Field(default_factory=list, max_length=50)
    filters: dict[Identifier, str | Number | bool] = Field(default_factory=dict, max_length=10)
    forecast_horizon: int = Field(default=14, ge=1, le=90)
    forecast_start: date | None = None
    forecast_model: Literal["auto", "seasonal_mean", "ridge_lag", "chronos2"] = "chronos2"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown IANA timezone") from None
        return value

    @model_validator(mode="after")
    def semantics(self):
        if self.end < self.start or (self.end - self.start).days > 1095:
            raise ValueError("Analysis period must be 0–1095 days")
        if self.forecast_start and not 1 <= (self.forecast_start - self.end).days <= 90:
            raise ValueError("Forecast start must follow history by 1–90 days")
        if self.metric_type == "STOCK" and self.aggregation not in {"LAST", "MIN", "MAX"}:
            raise ValueError("STOCK supports LAST/MIN/MAX; snapshots must not be summed")
        if self.metric_type == "STOCK" and self.missing_policy == "zero":
            raise ValueError("Missing stock is unknown, not zero")
        if self.metric_type == "FLOW" and self.missing_policy == "forward_fill":
            raise ValueError("FLOW cannot carry a previous period's quantity forward")
        if len({self.time_column, self.entity_column, self.target_column}) != 3:
            raise ValueError("Time, entity and target columns must differ")
        if "org_id" in self.filters:
            raise ValueError("Organization is bound by the server, not user configuration")
        return self


class ForecastRequest(DTO):
    config: AnalysisConfig
    allow_statistical_fallback: bool = False
    start_from_request_date: bool = False
    training_days: Literal[90, 180, 365] | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def calendar_window(self):
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("Supply both forecast dates")
        if self.start_date is not None:
            if self.start_from_request_date or not 0 <= (self.end_date - self.start_date).days < 90:
                raise ValueError("Choose one forecast window of 1–90 days")
        return self


class ProfileRequest(ProductRef):
    time_column: Identifier
    entity_column: Identifier
    timezone: str = "Asia/Ho_Chi_Minh"

    _timezone_valid = field_validator("timezone")(AnalysisConfig.valid_timezone.__func__)


class OverviewRequest(ProductRef):
    days: Literal[7, 14, 30, 90, 365] = 30
    entity: str | None = Field(default=None, min_length=1, max_length=80)
    compare_entities: list[str] = Field(default_factory=list, max_length=3)
    configuration_id: UUID | None = None
    stock: ProductRef | None = None
    inbound: ProductRef | None = None
    capacity: ProductRef | None = None
    catalog: ProductRef | None = None
    planning_days: Literal[7, 14, 30] = 14


class SimulationRequest(DTO):
    forecast_run_id: UUID
    snapshot_id: UUID | None = None
    sku: str = Field(min_length=1, max_length=80)
    warehouse_id: UUID
    stock: ProductRef
    inbound: ProductRef
    open_orders: ProductRef | None = None
    commitments: ProductRef | None = None
    additional_quantity: Number = Field(default=0, ge=0, le=1e9)
    arrival_date: date
    demand_multiplier: Number = Field(default=1, ge=0, le=5)
    demand_quantile: Literal["p50", "p90"] = "p50"
    inbound_delay_days: int = Field(default=0, ge=0, le=90)
    include_sensitivity: bool = False
    require_ml: bool = False
    save: bool = False

    @model_validator(mode="after")
    def paired_order_facts(self):
        if (self.open_orders is None) != (self.commitments is None):
            raise ValueError("Open orders and warehouse commitment facts must be supplied together")
        return self


class CapacityRequest(ProductRef):
    carrier_code: str = Field(min_length=1, max_length=50)
    start: date
    end: date
    additional_dispatches: int = Field(default=0, ge=0, le=100000)


class SourceRead(DTO):
    ref: ProductRef
    columns: list[Identifier]


class ForecastPoint(DTO):
    item_id: str
    timestamp: date
    p10: Number
    p50: Number
    p90: Number


class ForecastResult(DTO):
    id: UUID
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    config: AnalysisConfig
    points: list[ForecastPoint]
    model: dict[str, JsonValue]
    evidence: dict[str, JsonValue]
    sources: list[SourceRead]
    quality: dict[str, int]
    metrics: list[dict[str, JsonValue]]
    created_at: datetime


class AnalyticsResult(DTO):
    series: list[dict[str, JsonValue]]
    metrics: list[dict[str, JsonValue]]
    quality: dict[str, int]
    evidence: dict[str, JsonValue]


class OverviewResult(DTO):
    config: AnalysisConfig
    analytics: AnalyticsResult
    entities: list[str]
    entity: str
    source_start: date
    source_end: date
    source_age_days: int
    comparison: AnalyticsResult | None = None
    comparison_entities: list[str] = Field(default_factory=list)
    stocks: list[dict[str, JsonValue]] = Field(default_factory=list)
    stock_evidence: dict[str, JsonValue] | None = None
    portfolio: dict[str, JsonValue] | None = None
    capacity_summary: dict[str, JsonValue] | None = None
    related_issues: dict[str, int] = Field(default_factory=dict)
    entity_labels: dict[str, str] = Field(default_factory=dict)
    report_sources: list[SourceRead] = Field(default_factory=list)


class ScenarioMetrics(DTO):
    first_risk_date: date | None
    peak_shortage: Number
    peak_shortage_at_deadline: Number | None = None
    peak_supply_shortage: Number | None = None
    forecast_demand: Number
    fulfilled_quantity: Number
    fulfillment_percent: Number | None
    known_orders_at_risk: int | None
    known_unallocated_demand: Number | None = None
    demand_headroom_percent: Number | None = None


class ScenarioOutput(DTO):
    series: list[dict[str, JsonValue]]
    metrics: ScenarioMetrics
    orders: list[dict[str, JsonValue]] = Field(default_factory=list)


class PlanningRequest(DTO):
    demand: ProductRef
    stock: ProductRef
    inbound: ProductRef
    open_orders: ProductRef
    commitments: ProductRef
    catalog: ProductRef | None = None
    receipts: ProductRef | None = None
    schedule: ProductRef | None = None
    capacity: ProductRef | None = None
    warehouse_id: UUID | None = None
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    horizon: int = Field(default=7, ge=1, le=90)
    history_days: Literal[7, 14, 30, 90, 365] = 30
    history_frequency: Literal["auto", "day", "week"] = "auto"
    forecast_model: Literal["seasonal_mean", "ridge_lag", "chronos2"] = "chronos2"
    training_days: Literal[90, 180, 365] = 180
    history_start: date | None = None
    history_end: date | None = None

    @model_validator(mode="after")
    def history_bounds(self):
        if bool(self.history_start) != bool(self.history_end):
            raise ValueError("Both history bounds are required")
        if self.history_start and not 0 <= (self.history_end - self.history_start).days <= 365:
            raise ValueError("History range must contain 1–366 days")
        return self


class SensitivityCell(DTO):
    quantity: Number
    arrival_date: date
    peak_shortage: Number
    known_orders_at_risk: int | None
    first_risk_date: date | None


class SensitivityResult(DTO):
    quantities: list[Number] = Field(max_length=5)
    dates: list[date] = Field(max_length=5)
    cells: list[SensitivityCell] = Field(max_length=25)
    unit: str


class DecisionMeasure(DTO):
    peak_shortage: Number
    shortage_days: int
    ending_inventory: Number
    new_demand: Number
    known_orders_at_risk: int | None
    demand_headroom_percent: Number | None = None


class ScenarioDecision(DTO):
    reference: DecisionMeasure
    stressed: DecisionMeasure
    proposed: DecisionMeasure
    minimum_additional_quantity: Number
    latest_arrival_date: date | None
    remaining_late_orders_after_suggestion: int | None
    verdict: Literal[
        "no_extra_needed", "extra_without_risk_reduction", "covered", "partial", "still_at_risk"
    ]
    model_method: str


class ScenarioResult(DTO):
    id: UUID | None = None
    baseline: ScenarioOutput
    proposed: ScenarioOutput
    reference: ScenarioOutput | None = None
    decision: ScenarioDecision | None = None
    comparison: dict[str, JsonValue]
    sensitivity: SensitivityResult | None = None
    evidence: dict[str, JsonValue]
    sources: list[SourceRead]
    created_at: datetime
