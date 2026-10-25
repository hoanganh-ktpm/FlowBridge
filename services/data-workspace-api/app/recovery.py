# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, conditional recovery options from the same deterministic supply ledger."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .planning import moment
from .scenario_decision import measures


def recovery_options(result, request, evaluate, snapshot_at, timezone):
    decision = result["decision"]
    quantity = decision["minimum_additional_quantity"]
    before = decision["stressed"]
    if before["peak_shortage"] <= 1e-9 and not before["known_orders_at_risk"]:
        return []
    policy = result["evidence"]["preparation_policy"]
    unit = result["evidence"]["unit"]
    options = []
    affected = [order for order in result["baseline"].get("orders", []) if order["at_risk"]]
    selected = [
        order
        for order in affected
        if any(
            item["sku"] == request.sku and item["uom"] == unit and item["missing_quantity"] > 0
            for item in order["shortages_at_deadline"]
        )
    ]
    candidates = [moment(order["deadline"]) for order in selected]
    if before["first_shortage_date"] and (
        not candidates
        or before["first_shortage_date"]
        < min(candidates).astimezone(ZoneInfo(timezone)).date().isoformat()
    ):
        candidates = [
            datetime.combine(
                datetime.fromisoformat(before["first_shortage_date"]).date(),
                time.min,
                ZoneInfo(timezone),
            )
        ]
        candidates = [max(candidates[0], snapshot_at)]
    deadline = min(candidates) if candidates else None
    if quantity > 0 and deadline is not None and deadline >= snapshot_at:
        trial = measures(evaluate(quantity, deadline))
        options.append(
            {
                "kind": "additional_supply",
                "title": "Bổ sung nguồn hàng trước giờ cần",
                "quantity": quantity,
                "unit": unit,
                "arrival_at": deadline.isoformat(),
                "projected_shortage": trial["peak_shortage"],
                "remaining_at_risk_orders": trial["known_orders_at_risk"],
                "affected_order_ids": [order["order_id"] for order in selected],
                "requires_confirmation": [
                    "Kho xác nhận nguồn hàng và giờ sẵn sàng",
                    "Logistics xác nhận lịch tiếp nhận",
                ],
                "assumptions": [
                    "Lượng bổ sung là nguồn thêm trong mô phỏng, chưa phải hàng nhận thật.",
                    "Chưa xét giá, MOQ hoặc khả năng giao của nhà cung cấp.",
                    "Cần xác minh thời gian chuẩn bị của Kho."
                    if policy is None
                    else "Đã tính thời gian chuẩn bị theo chính sách Kho được công bố.",
                ],
                "can_fill_simulation": True,
            }
        )
    late = [
        order
        for order in result["proposed"].get("orders", [])
        if order["at_risk"] and order.get("fulfilled_at")
    ]
    if late and policy:
        for order in late[:1]:
            at = moment(order["fulfilled_at"]) + timedelta(minutes=policy["minutes"])
            options.append(
                {
                    "kind": "review_commitment",
                    "title": "Rà lại cam kết và lịch bàn giao",
                    "order_id": order["order_id"],
                    "suggested_handoff_at": at.isoformat(),
                    "requires_confirmation": [
                        "Sales thống nhất cam kết mới và cập nhật nguồn",
                        "Logistics xác nhận một lịch phù hợp",
                    ],
                    "assumptions": [
                        "Mốc tham khảo từ giờ có thể đủ hàng trong mô phỏng; chưa có lịch carrier.",
                        "Không tự đổi hạn đơn, chưa kết luận phương án mới khả thi.",
                    ],
                    "can_fill_simulation": False,
                }
            )
    if not options or (before["known_orders_at_risk"] and not selected):
        options.append(
            {
                "kind": "verify_constraints",
                "title": "Xác minh các điều kiện còn vướng",
                "requires_confirmation": [
                    "Rà hạn đã qua, mặt hàng khác cùng đơn và nguồn dữ liệu thiếu"
                ],
                "assumptions": [
                    "Bổ sung riêng mặt hàng đang chọn có thể chưa giải quyết được nguyên đơn."
                ],
                "can_fill_simulation": False,
            }
        )
    return options[:3]
