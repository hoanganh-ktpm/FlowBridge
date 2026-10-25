# SPDX-License-Identifier: GPL-3.0-or-later
"""A proposed carrier slot is a condition, distinct from a committed booking."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from .analytics import local_day, numeric
from .planning import moment
from .schemas import DTO


class HandoffPlan(DTO):
    carrier_code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    scheduled_handoff_at: datetime

    @field_validator("scheduled_handoff_at")
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Proposed carrier slot requires timezone")
        return value


def assess_slot(selected, schedule, calendar, policy, timezone, planned=None):
    reasons = []

    def missing(code, message):
        if code not in {item["code"] for item in reasons}:
            reasons.append({"code": code, "message": message})

    booked = [
        row for row in schedule if row["status"] == "ACCEPTED" and row["scheduled_handoff_at"]
    ]
    proposal = HandoffPlan.model_validate(planned).model_dump(mode="json") if planned else None
    slots = [proposal] if proposal else booked
    keys = {
        (r["carrier_code"], local_day(r["scheduled_handoff_at"], timezone).isoformat())
        for r in slots
    }
    capacities = [
        row for row in calendar if (row["carrier_code"], str(row["capacity_date"])) in keys
    ]
    matches = (
        not proposal
        or len(booked) <= 1
        and all(
            r["carrier_code"] == proposal["carrier_code"]
            and moment(r["scheduled_handoff_at"]) == moment(proposal["scheduled_handoff_at"])
            for r in booked
        )
    )
    deadline = moment(selected.get("handoff_deadline", selected["deadline"]))
    ready = moment(selected["fulfilled_at"]) if selected.get("fulfilled_at") else None
    feasible = bool(slots) and matches and policy is not None and ready is not None
    if not slots:
        missing("SCHEDULE_MISSING", "Chưa có lịch bàn giao để xác minh.")
    if not matches:
        missing("BOOKING_CONFLICT", "Lịch dự kiến khác lịch đã được đơn vị vận chuyển xác nhận.")
    if policy is None:
        missing("PREPARATION_UNKNOWN", "Kho chưa xác nhận thời gian chuẩn bị.")
    if ready is None:
        missing("SUPPLY_UNPROVEN", "Phương án chưa chứng minh đủ hàng cho toàn đơn.")
    for slot in slots:
        at = moment(slot["scheduled_handoff_at"])
        if at > deadline:
            missing("AFTER_DEADLINE", "Lịch bàn giao dự kiến sau hạn của đơn.")
        if ready is not None and policy is not None:
            prepared = ready + timedelta(minutes=policy["minutes"])
            if prepared > at:
                label = prepared.astimezone(ZoneInfo(timezone)).strftime("%H:%M %d/%m/%Y")
                missing(
                    "BEFORE_PREPARATION_COMPLETE",
                    "Lịch dự kiến sớm hơn mốc đủ hàng cộng thời gian chuẩn bị mà mô phỏng "
                    f"đang chứng minh: {label} (giờ Việt Nam). "
                    "Đây là mốc phân bổ theo hạn trong mô phỏng, chưa phải thời điểm "
                    "chuẩn bị sớm nhất có thể.",
                )
        feasible = (
            feasible and at <= deadline and ready + timedelta(minutes=policy["minutes"]) <= at
        )
    for carrier, day in keys:
        values = [
            r for r in capacities if r["carrier_code"] == carrier and str(r["capacity_date"]) == day
        ]
        if len(values) != 1:
            feasible = False
            missing("CAPACITY_UNKNOWN", "Chưa có một bản năng lực vận chuyển hợp lệ cho ngày chọn.")
            continue
        value = values[0]
        used, maximum = numeric(value["used_dispatches"]), numeric(value["max_dispatches"])
        # An actual matching booking is already included in used_dispatches.
        # Otherwise this proposed whole-order handoff needs one additional slot.
        needed = 1 if proposal and not booked else 0
        if value["status"] != "ACTIVE" or not 0 <= used <= maximum:
            missing(
                "CAPACITY_INVALID", "Năng lực vận chuyển chưa hoạt động hoặc số lượt không hợp lệ."
            )
        elif used + needed > maximum:
            missing("CAPACITY_FULL", "Ngày đã chọn không còn đủ lượt để nhận thêm đơn.")
        elif needed == 0 and used < 1:
            missing("BOOKING_UNPROVEN", "Lượt đã đặt chưa đối chiếu được với số lượt sử dụng.")
        feasible = (
            feasible
            and value["status"] == "ACTIVE"
            and 0 <= used <= maximum
            and used + needed <= maximum
            and (needed == 1 or used >= 1)
        )
    return {
        "confirmable": bool(feasible),
        "capacities": capacities,
        "contract_schedule": slots,
        "booking_status": "BOOKED" if booked else "PROPOSED_CAPACITY" if proposal else "UNKNOWN",
        "reasons": reasons,
    }
