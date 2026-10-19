# SPDX-License-Identifier: GPL-3.0-or-later
"""Inspectable feature-history cohort, separate from the TabICL in-context dataset."""

import hashlib
import json
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flowbridge.supplier_assistant.database_service import BUSINESS_TIMEZONE


class HistoryReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: Literal["supplier-product-unit-completed-v1"] = "supplier-product-unit-completed-v1"
    product_id: UUID
    supplier_id: UUID
    unit: str
    product_category: str
    feature_asof: datetime
    history_window_days: int = Field(ge=1, le=3650)
    exclude_item_id: UUID | None = None
    sample_count: int = Field(ge=0)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("feature_asof")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("HISTORY_CUTOFF_NEEDS_TIMEZONE")
        return value


class HistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: UUID
    supplier_id: UUID
    feature_asof: datetime | None = None
    reference: HistoryReference | None = None
    expected_count: int | None = Field(default=None, ge=0)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=20)

    @field_validator("feature_asof")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("HISTORY_CUTOFF_NEEDS_TIMEZONE")
        return value


class HistoryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purchase_order_id: UUID
    po_item_id: UUID
    po_number: str
    product_name: str
    unit: str
    ordered_quantity: str
    order_date: date
    requested_delivery_date: date
    completion_date: date
    completion_known_at: datetime
    lead_days: int
    days_late: int = Field(ge=0)
    delivered_late: bool


class HistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["success", "partial", "not_found", "changed"]
    reference: HistoryReference
    reference_matches: bool | None
    expected_count_matches: bool | None
    scope: Literal["company_procurement_reader"] = "company_procurement_reader"
    definition: str = (
        "Dòng hàng đã nhận đủ, cùng NCC–sản phẩm–đơn vị, trong cửa sổ "
        "lịch sử tại mốc chấm. Dùng tính hist_sample_count/lead time/tỷ lệ trễ; "
        "không phải toàn bộ context TabICL và không phải giải thích nhân quả của điểm."
    )
    items: list[HistoryRow]
    total_matches: int
    page: int
    page_size: int
    next_page: int | None
    warnings: list[str]

    @model_validator(mode="after")
    def coherent_page(self):
        expected = max(
            0, min(self.page_size, self.total_matches - (self.page - 1) * self.page_size)
        )
        more = self.page * self.page_size < self.total_matches
        if (
            not 1 <= self.page_size <= 20
            or self.page < 1
            or self.total_matches != self.reference.sample_count
            or len(self.items) != expected
            or self.next_page != (self.page + 1 if more else None)
            or len({r.po_item_id for r in self.items}) != len(self.items)
        ):
            raise ValueError("INVALID_HISTORY_PAGE")
        return self


def history_reference(
    group, *, product_id, supplier_id, unit, category, asof, history_days, exclude_item_id=None
) -> HistoryReference:
    rows = []
    for fact, outcome in group:
        rows.append(
            {
                "order_id": str(fact.order["purchase_order_id"]),
                "item_id": str(fact.item["po_item_id"]),
                "order_date": fact.order["order_date"].isoformat(),
                "quantity": str(fact.item["ordered_quantity"]),
                "deadline": fact.deadline().isoformat(),
                "completion": outcome[0].isoformat(),
                "known_at": outcome[1].isoformat(),
            }
        )
    fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return HistoryReference(
        product_id=product_id,
        supplier_id=supplier_id,
        unit=unit,
        product_category=category,
        feature_asof=asof,
        history_window_days=history_days,
        exclude_item_id=exclude_item_id,
        sample_count=len(group),
        fingerprint=fingerprint,
    )


def history_row(fact, outcome, product_name) -> HistoryRow:
    deadline = fact.deadline()
    issued = fact.order["order_date"].astimezone(BUSINESS_TIMEZONE).date()
    return HistoryRow(
        purchase_order_id=fact.order["purchase_order_id"],
        po_item_id=fact.item["po_item_id"],
        po_number=fact.order["po_number"],
        product_name=product_name,
        unit=fact.item["unit"],
        ordered_quantity=str(fact.item["ordered_quantity"]),
        order_date=issued,
        requested_delivery_date=deadline,
        completion_date=outcome[0],
        completion_known_at=outcome[1],
        lead_days=(outcome[0] - issued).days,
        days_late=max(0, (outcome[0] - deadline).days),
        delivered_late=outcome[0] > deadline,
    )
