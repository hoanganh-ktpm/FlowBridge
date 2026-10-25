# SPDX-License-Identifier: GPL-3.0-or-later
"""Check published cross-application identities without guessing SKU/unit mappings."""

from fastapi import HTTPException

from .analytics import numeric


def invalid(code, message):
    raise HTTPException(422, detail={"code": code, "message": message})


def validate_planning_facts(stocks, inbounds, rows, commitments, warehouse_id, catalog=None):
    warehouse = str(warehouse_id)
    balances = {}
    for row in stocks:
        if str(row["warehouse_id"]) != warehouse:
            continue
        sku = row["sku"]
        if sku in balances:
            invalid("STOCK_GRAIN_INVALID", "Tồn kho bị trùng mã hàng trong cùng kho.")
        on_hand, reserved = numeric(row["on_hand_quantity"]), numeric(row["reserved_quantity"])
        if min(on_hand, reserved) < 0 or reserved > on_hand:
            invalid("STOCK_BALANCE_INVALID", "Số tồn hoặc số hàng đã giữ không hợp lệ.")
        if (
            "available_quantity" in row
            and abs(numeric(row["available_quantity"]) - (on_hand - reserved)) > 1e-6
        ):
            invalid("STOCK_BALANCE_INVALID", "Tồn khả dụng không khớp tồn thực tế trừ hàng đã giữ.")
        balances[sku] = row["uom"]

    def identity(row):
        if row["sku"] not in balances:
            invalid(
                "MISSING_STOCK_COVERAGE",
                "Chưa có dòng tồn được xác minh cho mặt hàng cần tính; "
                "không thể mặc định số tồn bằng 0.",
            )
        if row["uom"] != balances[row["sku"]]:
            invalid("SOURCE_UOM_MISMATCH", "Đơn vị mặt hàng giữa các nguồn không khớp.")

    active_lines = {}
    for row in rows or []:
        if str(row["warehouse_id"]) != warehouse or row["status"] in {"CANCELLED", "COMPLETED"}:
            continue
        identity(row)
        key = (str(row["order_id"]), str(row["order_line_id"]))
        if key in active_lines:
            invalid("ORDER_GRAIN_INVALID", "Dòng đơn hàng bị trùng trong dữ liệu nguồn.")
        active_lines[key] = (row["sku"], row["uom"])
    for row in commitments or []:
        if str(row.get("warehouse_id", warehouse)) != warehouse:
            continue
        key = (str(row["order_id"]), str(row["order_line_id"]))
        if key in active_lines and active_lines[key] != (row["sku"], row["uom"]):
            invalid("COMMITMENT_IDENTITY_MISMATCH", "Dòng giữ/xuất hàng không khớp dòng đơn Sales.")
    for row in inbounds:
        if str(row["warehouse_id"]) == warehouse and row["status"] in {
            "APPROVED",
            "PARTIALLY_RECEIVED",
        }:
            identity(row)
    for row in catalog or []:
        if str(row["warehouse_id"]) == warehouse and row["sku"] in balances:
            identity(row)
    return balances
