# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-order supply warnings from the existing time-aware SKU projections."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .planning import moment


def order_supply_checks(baselines, known, headers, policy, end, snapshot_at):
    # Each baseline applies forecast demand to its own SKU. Taking another SKU's
    # shortage from an arbitrary baseline would omit that SKU's forecast demand.
    projections = {
        sku: {row["order_id"]: row for row in baseline["orders"]}
        for sku, baseline in baselines.items()
        if baseline is not None
    }
    remaining = {order["id"]: order["lines"] for order in known}
    boundary = datetime.combine(
        end + timedelta(days=1), datetime.min.time(), ZoneInfo("Asia/Ho_Chi_Minh")
    )
    checks = {}
    for order_id, header in headers.items():
        deadline = moment(header["requested_handoff_at"])
        ready_by = deadline - timedelta(minutes=policy["minutes"]) if policy else None
        check = {
            "status": "unknown",
            "shortages": [],
            "stock_ready_by": ready_by.isoformat() if ready_by else None,
            "preparation_minutes": policy["minutes"] if policy else None,
            "notes": [],
        }
        checks[order_id] = check
        if deadline >= boundary:
            check["status"] = "outside_horizon"
            check["notes"].append("Hạn bàn giao của đơn nằm ngoài khoảng thời gian đang dự báo.")
            continue
        complete = True
        for (sku, uom), _quantity in remaining.get(order_id, {}).items():
            projected = projections.get(sku, {}).get(order_id)
            if projected is None:
                complete = False
                continue
            check["shortages"].extend(
                dict(row)
                for row in projected["shortages_at_deadline"]
                if row["sku"] == sku and row["uom"] == uom and row["missing_quantity"] > 1e-9
            )
        if not complete:
            check["notes"].append("Chưa có dự báo đầy đủ cho tất cả mặt hàng của đơn.")
        if not policy:
            check["notes"].append(
                "Kho chưa xác nhận thời gian chuẩn bị; tạm đối chiếu tại hạn bàn giao."
            )
        expired = (ready_by or deadline) < snapshot_at
        if expired:
            check["notes"].append(
                "Mốc cần đủ hàng đã qua. Kết quả ước tính từ số liệu hiện tại, "
                "không xác nhận số tồn tại thời điểm đã qua."
            )
        check["status"] = (
            "risk"
            if check["shortages"]
            else "clear"
            if complete and policy and not expired
            else "unknown"
        )
    return checks
