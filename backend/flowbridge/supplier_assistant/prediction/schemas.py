# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .history import HistoryReference

FEATURE_VERSION = "tabicl_supplier_fixed_v1"
PREPROCESSING_VERSION = "supplier-features-v1"
CATEGORICAL = ("prediction_stage", "supplier_id", "product_category", "confirmation_state")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def decimal_string(value: object) -> object:
    if not isinstance(value, (str, Decimal)):
        raise ValueError("Use a decimal string")
    return value


Quantity = Annotated[
    Decimal,
    BeforeValidator(decimal_string),
    Field(gt=0, max_digits=18, decimal_places=4),
]
Price = Annotated[
    Decimal,
    BeforeValidator(decimal_string),
    Field(ge=0, max_digits=18, decimal_places=4),
]


class ScenarioRequest(StrictModel):
    product_id: UUID
    supplier_id: UUID
    quantity: Quantity
    requested_delivery_date: date


class RankRequest(StrictModel):
    product_id: UUID
    quantity: Quantity
    requested_delivery_date: date
    top_k: int = Field(default=3, ge=1, le=10, strict=True)
    ranking_policy: Literal["risk_first"] = "risk_first"
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    max_unit_price: Price | None = None

    @model_validator(mode="after")
    def price_currency(self) -> Self:
        if self.max_unit_price is not None and self.currency is None:
            raise ValueError("currency is required with max_unit_price")
        return self


class Features(StrictModel):
    # Field order is the model contract. Missing fields remain explicit JSON null.
    prediction_stage: Literal["PR_CREATED", "PO_MONITORING"]
    supplier_id: str = Field(min_length=1)
    product_category: str = Field(min_length=1)
    order_size_ratio: float = Field(gt=0)
    days_to_deadline: float
    non_working_days_to_deadline: float | None = Field(default=None, ge=0)
    hist_lead_p50_days: float | None = Field(default=None, ge=0)
    hist_lead_p90_days: float | None = Field(default=None, ge=0)
    hist_late_rate_smoothed: float | None = Field(default=None, ge=0, le=1)
    hist_sample_count: int = Field(ge=0, strict=True)
    other_open_order_count: int = Field(ge=0, strict=True)
    expected_issue_wait_days: float | None = Field(default=None, ge=0)
    remaining_size_ratio: float | None = Field(default=None, ge=0)
    elapsed_days_since_issue: float | None = Field(default=None, ge=0)
    confirmation_state: (
        Literal["issued", "waiting_confirmation", "confirmed", "rejected"] | None
    ) = None
    confirmation_wait_days: float | None = Field(default=None, ge=0)
    received_fraction: float | None = Field(default=None, ge=0, le=1)
    days_since_last_receipt: float | None = Field(default=None, ge=0)
    promise_margin_days: float | None = None

    @model_validator(mode="after")
    def stage_contract(self) -> Self:
        if self.prediction_stage == "PR_CREATED":
            if self.days_to_deadline < 0:
                raise ValueError("Scenario deadline has passed")
            if any(getattr(self, key) is not None for key in tuple(Features.model_fields)[12:]):
                raise ValueError("Monitoring features must be null at PR_CREATED")
        elif self.expected_issue_wait_days is not None or self.elapsed_days_since_issue is None:
            raise ValueError("Invalid monitoring issue time")
        if (
            self.hist_lead_p50_days is not None
            and self.hist_lead_p90_days is not None
            and self.hist_lead_p90_days < self.hist_lead_p50_days
        ):
            raise ValueError("Invalid historical quantiles")
        return self


FEATURE_NAMES = tuple(Features.model_fields)


class FeatureSnapshot(StrictModel):
    x: Features
    feature_asof: datetime
    product_id: UUID
    quantity: Quantity
    unit: str
    requested_delivery_date: date
    metadata: dict = Field(default_factory=dict)


class RiskResult(StrictModel):
    status: Literal["success", "insufficient_data"]
    risk_score: float | None = Field(default=None, ge=0, le=1)
    risk_level: Literal["low", "high"] | None = None
    risk_threshold: float
    score_is_calibrated: Literal[False] = False
    calibrated_late_probability: None = None
    hist_sample_count: int | None = None
    history_reference: HistoryReference | None = None
    error_code: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Trace(StrictModel):
    feature_asof: datetime
    feature_schema_version: str = FEATURE_VERSION
    preprocessing_version: str = PREPROCESSING_VERSION
    model_version: str
    context_dataset_version: str
    prediction_id: UUID | None = None
    persisted: bool = False


class ScenarioResponse(StrictModel):
    scenario: ScenarioRequest
    result: RiskResult
    trace: Trace


class Candidate(StrictModel):
    supplier_id: UUID
    supplier_name: str
    unit_price: str
    currency: str
    quoted_lead_days: int | None
    rank: int | None = None
    result: RiskResult
    reason_codes: list[str] = Field(default_factory=list)


class RankResponse(StrictModel):
    status: Literal["success", "partial", "insufficient_data", "no_eligible_suppliers"]
    trace: Trace
    ranking_policy_version: str = "risk-first-v1"
    candidates: list[Candidate]
    unscored: list[Candidate] = Field(default_factory=list)
    excluded: list[dict] = Field(default_factory=list)
    considered_count: int
    warnings: list[str] = Field(default_factory=list)


class ItemRisk(StrictModel):
    po_item_id: UUID
    product_id: UUID
    requested_delivery_date: date
    result: RiskResult
    trace: Trace | None = None
    remaining_value: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None


class OrderRisk(StrictModel):
    purchase_order_id: UUID
    aggregation_policy: Literal["max_open_item_v1", "remaining_value_weighted_v2"] = (
        "remaining_value_weighted_v2"
    )
    status: Literal["success", "partial", "insufficient_data", "no_open_items"]
    representative_risk_score: float | None = None
    aggregation_definition: str = ""
    highest_risk_item_id: UUID | None = None
    highest_risk_item_deadline: date | None = None
    highest_item_risk_score: float | None = Field(default=None, ge=0, le=1)
    observed_value_weighted_score: float | None = Field(default=None, ge=0, le=1)
    scored_value_coverage: float | None = Field(default=None, ge=0, le=1)
    high_risk_item_count: int | None = Field(default=None, ge=0)
    unscored_item_count: int | None = Field(default=None, ge=0)
    score_is_calibrated: Literal[False] = False
    is_complete: bool
    items: list[ItemRisk]
    warnings: list[str] = Field(default_factory=list)
