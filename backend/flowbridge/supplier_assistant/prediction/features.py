# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any
from uuid import UUID

from flowbridge.shared.errors import CoreError, ErrorCode, ErrorDetail
from flowbridge.supplier_assistant.database_service import BUSINESS_TIMEZONE

from .schemas import Features, FeatureSnapshot


def history_samples(
    facts: list["OrderFact"],
    *,
    product_id: UUID,
    supplier_id: UUID,
    unit: str,
    category: str,
    asof: datetime,
    history_days: int,
    exclude_item_id: UUID | None = None,
) -> list[tuple["OrderFact", tuple[date, datetime]]]:
    """The exact completed supplier/product/unit cohort behind hist_sample_count."""
    cutoff = asof - timedelta(days=history_days)
    result = []
    for fact in facts:
        if (
            cutoff <= fact.order["order_date"] < asof
            and fact.item["po_item_id"] != exclude_item_id
            and fact.order["status"] not in {"cancelled", "rejected"}
            and fact.item["item_status"] != "cancelled"
            and fact.item["product_category_snapshot"] == category
            and fact.item["product_id"] == product_id
            and fact.item["unit"] == unit
            and fact.order["supplier_id"] == supplier_id
        ):
            outcome = fact.outcome(asof)
            if outcome is not None:
                result.append((fact, outcome))
    return sorted(
        result, key=lambda row: (row[0].order["order_date"], str(row[0].item["po_item_id"]))
    )


@dataclass(frozen=True)
class OrderFact:
    item: dict[str, Any]
    order: dict[str, Any]
    schedules: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    confirmations: list[dict[str, Any]] = field(default_factory=list)
    promises: list[dict[str, Any]] = field(default_factory=list)

    def known_receipts(self, asof: datetime) -> list[dict[str, Any]]:
        day = asof.astimezone(BUSINESS_TIMEZONE).date()
        return [
            row
            for row in self.receipts
            if row["received_at"] <= asof and row["actual_delivery_date"] <= day
        ]

    def outcome(self, asof: datetime) -> tuple[date, datetime] | None:
        # First actual date at which accepted cumulative quantity reaches Q.
        quantity = Decimal(0)
        known_at = self.order["order_date"]
        for row in sorted(
            self.known_receipts(asof),
            key=lambda r: (r["actual_delivery_date"], r["received_at"]),
        ):
            quantity += row["received_quantity"]
            known_at = max(known_at, row["received_at"])
            if quantity >= self.item["ordered_quantity"]:
                return row["actual_delivery_date"], known_at
        return None

    def deadline(self) -> date:
        dates = {row["requested_delivery_date"] for row in self.schedules}
        if (
            len(dates) != 1
            or sum((row["scheduled_quantity"] for row in self.schedules), Decimal(0))
            != self.item["ordered_quantity"]
        ):
            raise CoreError(ErrorCode.INSUFFICIENT_DATA)
        return next(iter(dates))


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    index = int(position)
    return ordered[index] + (ordered[min(index + 1, len(ordered) - 1)] - ordered[index]) * (
        position - index
    )


class FeatureBuilder:
    def __init__(
        self,
        *,
        history_days: int = 180,
        smoothing: float = 12,
        quantity_references: dict[str, dict] | None = None,
        receipt_coverage_confirmed: bool = False,
    ) -> None:
        self.history_days = history_days
        self.smoothing = smoothing
        self.quantity_references = quantity_references or {}
        self.receipt_coverage_confirmed = receipt_coverage_confirmed

    def build(
        self,
        *,
        product: dict,
        supplier_id: UUID,
        quantity: Decimal,
        unit: str,
        deadline: date,
        asof: datetime,
        facts: list[OrderFact],
        current: OrderFact | None = None,
    ) -> FeatureSnapshot:
        category = product["product_category"]
        product_id = product["product_id"]
        cutoff = asof - timedelta(days=self.history_days)
        historical = [
            f
            for f in facts
            if cutoff <= f.order["order_date"] < asof
            and (current is None or f.item["po_item_id"] != current.item["po_item_id"])
            and f.order["status"] not in {"cancelled", "rejected"}
            and f.item["item_status"] != "cancelled"
        ]
        same_product = [
            f for f in historical if f.item["product_id"] == product_id and f.item["unit"] == unit
        ]
        sizes = [f.item["ordered_quantity"] for f in same_product]
        reference = self.quantity_references.get(str(product_id), {})
        if sizes:
            q_ref, reference_source = median(sizes), "issued-product-unit-median"
        elif reference.get("unit") == unit:
            q_ref = Decimal(str(reference.get("quantity", "0")))
            reference_source = "operator-context-manifest"
        else:
            q_ref, reference_source = Decimal(0), "unavailable"
        if not q_ref.is_finite() or q_ref <= 0:
            raise CoreError(
                ErrorCode.INSUFFICIENT_DATA,
                details=(ErrorDetail(field="q_ref", reason="No positive quantity reference"),),
            )
        pool = [
            (f, f.outcome(asof))
            for f in historical
            if f.item["product_category_snapshot"] == category
        ]
        pool = [(f, outcome) for f, outcome in pool if outcome is not None]
        group = history_samples(
            facts,
            product_id=product_id,
            supplier_id=supplier_id,
            unit=unit,
            category=category,
            asof=asof,
            history_days=self.history_days,
            exclude_item_id=current.item["po_item_id"] if current else None,
        )
        leads = [
            float((c[0] - f.order["order_date"].astimezone(BUSINESS_TIMEZONE).date()).days)
            for f, c in group
        ]
        pool_rate = sum(c[0] > f.deadline() for f, c in pool) / len(pool) if pool else None
        n = len(group)
        k = sum(c[0] > f.deadline() for f, c in group)
        late_rate = (
            (k + self.smoothing * pool_rate) / (n + self.smoothing)
            if pool_rate is not None
            else None
        )
        backlog = sum(
            f.order["supplier_id"] == supplier_id
            and f.order["order_date"] <= asof
            and f.order["status"] not in {"cancelled", "rejected", "delivered"}
            and f.item["item_status"] not in {"cancelled", "completed"}
            and (current is None or f.item["po_item_id"] != current.item["po_item_id"])
            and f.outcome(asof) is None
            for f in facts
        )
        x = dict(
            prediction_stage="PO_MONITORING" if current else "PR_CREATED",
            supplier_id=str(supplier_id),
            product_category=category,
            order_size_ratio=float(quantity / q_ref),
            days_to_deadline=(deadline - asof.astimezone(BUSINESS_TIMEZONE).date()).days,
            hist_lead_p50_days=quantile(leads, 0.5),
            hist_lead_p90_days=quantile(leads, 0.9),
            hist_late_rate_smoothed=late_rate,
            hist_sample_count=n,
            other_open_order_count=backlog,
        )
        warnings = ["SUPPLIER_CALENDAR_UNAVAILABLE"]
        if not group:
            warnings.append("NO_SUPPLIER_PRODUCT_HISTORY")
        if current:
            receipts = current.known_receipts(asof)
            received = sum((r["received_quantity"] for r in receipts), Decimal(0))
            if received > quantity:
                raise CoreError(ErrorCode.INSUFFICIENT_DATA)
            confirmations = sorted(
                (c for c in current.confirmations if c["responded_at"] <= asof),
                key=lambda c: c["responded_at"],
            )
            confirmation = confirmations[-1] if confirmations else None
            issued = current.order["order_date"]
            elapsed = (asof - issued).total_seconds() / 86400
            state = (
                "confirmed"
                if confirmation and confirmation["decision"] == "confirmed"
                else ("rejected" if confirmation else "waiting_confirmation")
            )
            x.update(
                elapsed_days_since_issue=elapsed,
                confirmation_state=state,
                confirmation_wait_days=(
                    (
                        (confirmation["responded_at"] if confirmation else asof) - issued
                    ).total_seconds()
                    / 86400
                ),
                days_since_last_receipt=(
                    (
                        asof.astimezone(BUSINESS_TIMEZONE).date()
                        - max(r["actual_delivery_date"] for r in receipts)
                    ).days
                    if receipts
                    else None
                ),
            )
            if self.receipt_coverage_confirmed:
                x.update(
                    remaining_size_ratio=float((quantity - received) / q_ref),
                    received_fraction=float(received / quantity),
                )
            else:
                warnings.append("RECEIPT_COVERAGE_UNCONFIRMED")
            margin = self.promise_margin(current, receipts, asof, deadline)
            x["promise_margin_days"] = margin
        from .history import history_reference

        return FeatureSnapshot(
            x=Features(**x),
            feature_asof=asof,
            product_id=product_id,
            quantity=quantity,
            unit=unit,
            requested_delivery_date=deadline,
            metadata={
                "q_ref": str(q_ref),
                "q_ref_source": reference_source,
                "history_window_days": self.history_days,
                "smoothing_kappa": self.smoothing,
                "history_pool": "category",
                "history_group": "supplier-product-unit",
                "history_source": "issued-items-and-append-only-accepted-receipts",
                "history_reference": history_reference(
                    group,
                    product_id=product_id,
                    supplier_id=supplier_id,
                    unit=unit,
                    category=category,
                    asof=asof,
                    history_days=self.history_days,
                    exclude_item_id=current.item["po_item_id"] if current else None,
                ).model_dump(mode="json"),
                "receipt_coverage_confirmed": self.receipt_coverage_confirmed,
                "warnings": warnings,
            },
        )

    @staticmethod
    def promise_margin(
        fact: OrderFact,
        receipts: list[dict],
        asof: datetime,
        deadline: date,
    ) -> float | None:
        confirmed = {
            c["confirmation_id"]
            for c in fact.confirmations
            if c["responded_at"] <= asof and c["decision"] == "confirmed"
        }
        dates = []
        for schedule in fact.schedules:
            received = sum(
                (
                    r["received_quantity"]
                    for r in receipts
                    if r["schedule_id"] == schedule["schedule_id"]
                ),
                Decimal(0),
            )
            remaining = schedule["scheduled_quantity"] - received
            if remaining <= 0:
                continue
            promises = [
                p
                for p in fact.promises
                if p["schedule_id"] == schedule["schedule_id"]
                and p["confirmation_id"] in confirmed
                and p["recorded_at"] <= asof
            ]
            if not promises:
                return None
            latest = max(promises, key=lambda p: (p["recorded_at"], p["revision_no"]))
            if (
                latest["promised_delivery_date"] is None
                or latest["confirmed_quantity"] is None
                or latest["confirmed_quantity"] < remaining
            ):
                return None
            dates.append(latest["promised_delivery_date"])
        return float((max(dates) - deadline).days) if dates else None
