# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only delivery counts from original schedules and accepted goods receipts."""

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
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
        valid, outcomes, received_any = bool(lines), [], False
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
        if not valid or not outcomes:
            counts["unknown_schedule"] += 1
        elif all(completion is not None for _, completion in outcomes):
            late = any(completion > deadline for deadline, completion in outcomes)
            counts["completed_late" if late else "completed_on_time"] += 1
        else:
            overdue = any(
                completion is None and deadline < day for deadline, completion in outcomes
            )
            counts["open_overdue" if overdue else "open_not_due"] += 1
            counts["partial_open"] += int(received_any)
    return DeliveryCounts(**counts)


async def calculate_statistics(
    sessions, identity: VerifiedIdentity, task_id: UUID, supplier_id: UUID
) -> DeliveryStatistics:
    async with sessions() as session, session.begin():
        # Every table is read within one repeatable, read-only PostgreSQL snapshot.
        await session.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        repo = SupplierRepository(session)
        policy = SupplierPolicy(repo)
        await policy.authenticate(identity)
        policy.authorize("AI_SERVICE")  # Active global DB grant, not a role supplied in JSON.
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
        counts = classify_orders(orders, items, schedules, receipts, as_of)
        completed = counts.completed_on_time + counts.completed_late
        day = as_of.astimezone(BUSINESS_TIMEZONE).date()
        warnings = ["CANCELLATION_STATUS_IS_CURRENT_NOT_HISTORIZED"]
        if counts.unknown_schedule:
            warnings.append("UNCLASSIFIABLE_ORDERS")
        return DeliveryStatistics(
            supplier_id=supplier_id,
            supplier_code=supplier["supplier_code"],
            supplier_name=supplier["supplier_name"],
            as_of=as_of,
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
            metric_definition="Đếm mỗi đơn một lần. "
            "Đúng hạn: mọi lịch giao đã nhận đủ theo hạn gốc; "
            "trễ: đã nhận đủ nhưng ít nhất một lịch giao hoàn tất sau hạn gốc.",
            denominator_definition="Tỷ lệ trễ = đơn đã nhận đủ trễ / tổng đơn đã nhận đủ. "
            "Đơn mở không vào mẫu số; đơn hủy/từ chối hiện tại bị loại. "
            "Biên nhận phải được ghi nhận và giao thực tế trước mốc chốt.",
        )
