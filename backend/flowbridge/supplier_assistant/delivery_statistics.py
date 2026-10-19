# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only delivery counts from original schedules and accepted goods receipts."""

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.shared.errors import ErrorCode
from flowbridge.supplier_assistant.database_service import BUSINESS_TIMEZONE
from flowbridge.supplier_assistant.policy import SupplierPolicy, require
from flowbridge.supplier_assistant.repository import SupplierRepository, table


class StatisticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: UUID
    include_orders: bool = False
    delivery_filter: Literal["late", "completed_late", "open_overdue", "all"] = "late"
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=5, ge=1, le=10)


class DeliveryOrdersRequest(StatisticsRequest):
    include_orders: Literal[True] = True
    purchase_order_id: UUID | None = None


class DeliverySchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule_id: UUID
    product_name: str
    unit: str
    scheduled_quantity: str
    received_quantity: str
    requested_delivery_date: date
    completion_date: date | None
    days_late: int = Field(ge=0)
    receipt_ids: list[UUID]
    receipts_truncated: bool = False


class DeliveryOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purchase_order_id: UUID
    po_number: str
    delivery_status: Literal[
        "completed_on_time", "completed_late", "open_overdue", "open_not_due", "unknown_schedule"
    ]
    max_days_late: int = Field(ge=0)
    late_schedule_count: int = Field(ge=0)
    details_truncated: bool
    schedules: list[DeliverySchedule]


class DeliveryOrderPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purchase_order_id: UUID | None = None
    delivery_filter: Literal["late", "completed_late", "open_overdue", "all"]
    items: list[DeliveryOrder]
    total_matches: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=10)
    has_more: bool
    next_page: int | None

    @model_validator(mode="after")
    def pagination(self):
        if self.purchase_order_id is not None and (
            self.total_matches > 1
            or any(row.purchase_order_id != self.purchase_order_id for row in self.items)
        ):
            raise ValueError("DELIVERY_ORDER_SELECTION_MISMATCH")
        more = self.page * self.page_size < self.total_matches
        expected = max(
            0, min(self.page_size, self.total_matches - (self.page - 1) * self.page_size)
        )
        if (
            len(self.items) != expected
            or self.has_more != more
            or self.next_page != (self.page + 1 if more else None)
            or len({row.purchase_order_id for row in self.items}) != len(self.items)
        ):
            raise ValueError("INVALID_DELIVERY_ORDER_PAGE")
        for row in self.items:
            if self.delivery_filter == "late" and row.max_days_late <= 0:
                raise ValueError("NON_LATE_ORDER_IN_LATE_PAGE")
            if (
                self.delivery_filter not in {"late", "all"}
                and row.delivery_status != self.delivery_filter
            ):
                raise ValueError("DELIVERY_ORDER_FILTER_MISMATCH")
        return self


class DeliveryCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    completed_on_time: int = Field(ge=0)
    completed_late: int = Field(ge=0)
    open_overdue: int = Field(ge=0)
    open_not_due: int = Field(ge=0)
    unknown_schedule: int = Field(ge=0)
    partial_open: int = Field(ge=0)
    excluded_cancelled: int = Field(ge=0)


class DeliveryStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    supplier_id: UUID
    supplier_code: str
    supplier_name: str
    as_of: datetime
    issued_from: date
    issued_to: date
    scope: str = "global_ai_service"
    count_unit: str = "đơn đặt hàng"
    total_eligible_orders: int = Field(ge=0)
    completed_orders: int = Field(ge=0)
    late_rate_percent: float | None
    counts: DeliveryCounts
    metric_definition: str
    denominator_definition: str
    complete: bool
    warnings: list[str]
    orders: DeliveryOrderPage | None = None

    @model_validator(mode="after")
    def totals(self):
        c = self.counts
        if self.as_of.tzinfo is None or self.issued_from > self.issued_to:
            raise ValueError("INVALID_STATISTICS_CUTOFF")
        if self.completed_orders != c.completed_on_time + c.completed_late:
            raise ValueError("INVALID_COMPLETED_DENOMINATOR")
        if (
            self.total_eligible_orders
            != (self.completed_orders + c.open_overdue + c.open_not_due + c.unknown_schedule)
            or c.partial_open > c.open_overdue + c.open_not_due
        ):
            raise ValueError("OVERLAPPING_ORDER_COUNTS")
        expected = late_percent(c.completed_late, self.completed_orders)
        if self.late_rate_percent != expected:
            raise ValueError("INVALID_LATE_RATE")
        return self


def late_percent(late: int, completed: int) -> float | None:
    if not completed:
        return None
    return float((Decimal(100) * late / completed).quantize(Decimal("0.01"), ROUND_HALF_UP))


def classify_orders(
    orders: list[dict],
    items: list[dict],
    schedules: list[dict],
    receipts: list[dict],
    as_of: datetime,
    *,
    details: list[dict] | None = None,
) -> DeliveryCounts:
    """A PO is on time only when every original schedule is fulfilled on time."""
    day = as_of.astimezone(BUSINESS_TIMEZONE).date()
    by_order, by_item, by_schedule = defaultdict(list), defaultdict(list), defaultdict(list)
    for item in items:
        by_order[item["purchase_order_id"]].append(item)
    for schedule in schedules:
        if schedule["created_at"] <= as_of:
            by_item[schedule["po_item_id"]].append(schedule)
    for receipt in receipts:
        if receipt["received_at"] <= as_of and receipt["actual_delivery_date"] <= day:
            by_schedule[receipt["schedule_id"]].append(receipt)
    counts = dict.fromkeys(DeliveryCounts.model_fields, 0)
    for order in orders:
        if order["order_date"] > as_of:
            continue
        if order["status"] in {"cancelled", "rejected"}:
            counts["excluded_cancelled"] += 1
            continue
        lines = by_order[order["purchase_order_id"]]
        valid, outcomes, received_any, schedule_details = bool(lines), [], False, []
        for line in lines:
            plans = by_item[line["po_item_id"]]
            if (
                not plans
                or sum((p["scheduled_quantity"] for p in plans), Decimal(0))
                != line["ordered_quantity"]
            ):
                valid = False
            for plan in plans:
                quantity, completion = Decimal(0), None
                for receipt in sorted(
                    by_schedule[plan["schedule_id"]],
                    key=lambda r: (
                        r["actual_delivery_date"],
                        r["received_at"],
                        str(r["receipt_id"]),
                    ),
                ):
                    quantity += receipt["received_quantity"]
                    received_any |= receipt["received_quantity"] > 0
                    if completion is None and quantity >= plan["scheduled_quantity"]:
                        completion = receipt["actual_delivery_date"]
                outcomes.append((plan["requested_delivery_date"], completion))
                late_days = max(0, ((completion or day) - plan["requested_delivery_date"]).days)
                schedule_details.append(
                    {
                        "schedule_id": plan["schedule_id"],
                        "product_name": line.get("product_name", "Chưa có tên hàng"),
                        "unit": line.get("unit", "Chưa có đơn vị"),
                        "scheduled_quantity": str(plan["scheduled_quantity"]),
                        "received_quantity": str(quantity),
                        "requested_delivery_date": plan["requested_delivery_date"],
                        "completion_date": completion,
                        "days_late": late_days,
                        "receipt_ids": [
                            r["receipt_id"] for r in by_schedule[plan["schedule_id"]][:5]
                        ],
                        "receipts_truncated": len(by_schedule[plan["schedule_id"]]) > 5,
                    }
                )
        if not valid or not outcomes:
            status = "unknown_schedule"
        elif all(completion is not None for _, completion in outcomes):
            late = any(completion > deadline for deadline, completion in outcomes)
            status = "completed_late" if late else "completed_on_time"
        else:
            overdue = any(
                completion is None and deadline < day for deadline, completion in outcomes
            )
            status = "open_overdue" if overdue else "open_not_due"
            counts["partial_open"] += int(received_any)
        counts[status] += 1
        if details is not None:
            late_plans = [s for s in schedule_details if s["days_late"] > 0]
            ordered_plans = sorted(
                schedule_details, key=lambda s: (-s["days_late"], str(s["schedule_id"]))
            )
            details.append(
                {
                    "purchase_order_id": order["purchase_order_id"],
                    "po_number": order["po_number"],
                    "delivery_status": status,
                    "max_days_late": max((s["days_late"] for s in late_plans), default=0)
                    if valid
                    else 0,
                    "late_schedule_count": len(late_plans) if valid else 0,
                    "details_truncated": len(ordered_plans) > 5,
                    "schedules": ordered_plans[:5],
                }
            )
    return DeliveryCounts(**counts)


def order_page(details: list[dict], options: StatisticsRequest) -> DeliveryOrderPage:
    selected = getattr(options, "purchase_order_id", None)
    rows = [
        row
        for row in details
        if (selected is None or row["purchase_order_id"] == selected)
        and (
            options.delivery_filter == "all"
            or (options.delivery_filter == "late" and row["max_days_late"] > 0)
            or row["delivery_status"] == options.delivery_filter
        )
    ]
    rows.sort(key=lambda r: (-r["max_days_late"], r["po_number"], str(r["purchase_order_id"])))
    more = options.page * options.page_size < len(rows)
    return DeliveryOrderPage(
        purchase_order_id=selected,
        delivery_filter=options.delivery_filter,
        items=rows[(options.page - 1) * options.page_size : options.page * options.page_size],
        total_matches=len(rows),
        page=options.page,
        page_size=options.page_size,
        has_more=more,
        next_page=options.page + 1 if more else None,
    )


async def calculate_statistics(
    sessions,
    identity: VerifiedIdentity,
    task_id: UUID | None,
    supplier_id: UUID,
    options: StatisticsRequest | None = None,
) -> DeliveryStatistics:
    async with sessions() as session, session.begin():
        # Every table is read within one repeatable, read-only PostgreSQL snapshot.
        await session.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        repo = SupplierRepository(session)
        policy = SupplierPolicy(repo)
        await policy.authenticate(identity)
        order_scope, scope = [], "global_ai_service"
        if task_id is None:
            # Interactive order details use the human's existing PR/PO read scope.
            # Supplier statistics' AI credential must never widen that scope.
            policy.authorize("EMPLOYEE", read=True)
            requests = table("purchase_requests")
            order_scope = [
                table("purchase_orders").c.request_id.in_(
                    sa.select(requests.c.request_id).where(*policy.request_conditions())
                )
            ]
            if isinstance(options, DeliveryOrdersRequest) and options.purchase_order_id:
                # Exact order selection is ANDed with caller scope and supplier;
                # a UUID must never widen the existing read policy.
                order_scope.append(
                    table("purchase_orders").c.purchase_order_id == options.purchase_order_id
                )
            as_of = datetime.now(UTC)
            scope = (
                "company_procurement_reader"
                if policy.has("PROCUREMENT_VIEWER")
                else "caller_visible_orders"
            )
        else:
            policy.authorize("AI_SERVICE")  # Active DB grant, not a JSON role.
            task = await repo.get("analysis_tasks", task_id)
            request = await policy.request(task["request_id"])
            require(task["status"] in {"queued", "running", "succeeded"})
            require(request["status"] != "cancelled", ErrorCode.REQUEST_CHANGED)
            require(task["input_revision"] == request["input_revision"], ErrorCode.REQUEST_CHANGED)
            as_of = task["feature_asof"]
            require(as_of <= datetime.now(UTC), ErrorCode.REQUEST_CHANGED)
            department = await repo.get("departments", request["department_id"])
            require(department["status"] == "active", ErrorCode.FORBIDDEN)
        supplier = await repo.get("suppliers", supplier_id)

        async def bounded(name, *conditions, maximum=20000):
            rows = await repo.rows(name, *conditions, limit=maximum + 1)
            require(len(rows) <= maximum, ErrorCode.INSUFFICIENT_DATA)
            return rows

        orders = await bounded(
            "purchase_orders",
            table("purchase_orders").c.supplier_id == supplier_id,
            table("purchase_orders").c.order_date <= as_of,
            table("purchase_orders").c.created_at <= as_of,
            *order_scope,
            maximum=5000,
        )
        items, schedules, receipts = [], [], []
        if orders:
            items = await bounded(
                "purchase_order_items",
                table("purchase_order_items").c.purchase_order_id.in_(
                    [o["purchase_order_id"] for o in orders]
                ),
            )
        if items:
            if options and options.include_orders:
                products = await bounded(
                    "products", table("products").c.product_id.in_([i["product_id"] for i in items])
                )
                names = {p["product_id"]: p["product_name"] for p in products}
                items = [
                    {**i, "product_name": names.get(i["product_id"], "Chưa có tên hàng")}
                    for i in items
                ]
            schedules = await bounded(
                "purchase_order_schedules",
                table("purchase_order_schedules").c.po_item_id.in_(
                    [i["po_item_id"] for i in items]
                ),
                table("purchase_order_schedules").c.created_at <= as_of,
            )
        if schedules:
            receipts = await bounded(
                "goods_receipts",
                table("goods_receipts").c.schedule_id.in_([s["schedule_id"] for s in schedules]),
                table("goods_receipts").c.received_at <= as_of,
            )
        details = [] if options and options.include_orders else None
        counts = classify_orders(orders, items, schedules, receipts, as_of, details=details)
        completed = counts.completed_on_time + counts.completed_late
        day = as_of.astimezone(BUSINESS_TIMEZONE).date()
        warnings = ["CANCELLATION_STATUS_IS_CURRENT_NOT_HISTORIZED"]
        if counts.unknown_schedule:
            warnings.append("UNCLASSIFIABLE_ORDERS")
        if details is not None:
            warnings.append("PRODUCT_NAMES_ARE_CURRENT_CATALOG_LABELS")
        return DeliveryStatistics(
            supplier_id=supplier_id,
            supplier_code=supplier["supplier_code"],
            supplier_name=supplier["supplier_name"],
            as_of=as_of,
            scope=scope,
            issued_from=min(
                (o["order_date"].astimezone(BUSINESS_TIMEZONE).date() for o in orders), default=day
            ),
            issued_to=day,
            total_eligible_orders=len(orders) - counts.excluded_cancelled,
            completed_orders=completed,
            late_rate_percent=late_percent(counts.completed_late, completed),
            counts=counts,
            complete=not bool(counts.unknown_schedule),
            warnings=warnings,
            orders=order_page(details, options) if details is not None else None,
            metric_definition="Đếm mỗi đơn một lần. "
            "Đúng hạn: mọi lịch giao đã nhận đủ theo hạn gốc; "
            "trễ: đã nhận đủ nhưng ít nhất một lịch giao hoàn tất sau hạn gốc.",
            denominator_definition="Tỷ lệ trễ = đơn đã nhận đủ trễ / tổng đơn đã nhận đủ. "
            "Đơn mở không vào mẫu số; đơn hủy/từ chối hiện tại bị loại. "
            "Biên nhận phải được ghi nhận và giao thực tế trước mốc chốt.",
        )
