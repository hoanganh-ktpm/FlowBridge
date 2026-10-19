# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import re

AGENT_LABELS = {
    "central": "Agent trung tâm",
    "database": "Dữ liệu",
    "prediction": "Dự đoán",
    "knowledge": "Tri thức RAG",
}
TOOL_LABELS = {
    "hoi_du_lieu": "Gọi agent Dữ liệu",
    "hoi_du_doan": "Gọi agent Dự đoán",
    "hoi_tri_thuc": "Gọi agent Tri thức RAG",
    "catalog_resolve_entities": "Xác minh sản phẩm / nhà cung cấp",
    "catalog_list": "Tra cứu danh mục",
    "catalog_read_offers": "Đọc báo giá",
    "purchase_get_request": "Đọc phiếu yêu cầu mua",
    "purchase_get_order": "Đọc đơn đặt hàng",
    "prediction_rank_suppliers": "Xếp hạng nhà cung cấp",
    "prediction_score_supplier": "Chấm điểm nhà cung cấp",
    "prediction_get_order_risk": "Dự đoán rủi ro đơn hàng",
    "prediction_get_result": "Đọc kết quả dự đoán",
    "knowledge_search_supplier": "Tìm tài liệu nhà cung cấp",
    "supplier_delivery_statistics": "Thống kê giao hàng",
}


def error_detail(code: str, trace_id: str) -> dict:
    # Never expose exception messages, URLs, SQL, model prompts or arbitrary tool error text.
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", code):
        code = "STEP_FAILED"
    return {
        "code": code,
        "message": "Bước xử lý này không hoàn tất; xem mã lỗi để đối chiếu.",
        "retryable": False,
        "trace_id": trace_id,
    }


def outcome(status: str) -> str:
    if status in {"needs_input", "ambiguous", "not_found"}:
        return "needs_input"
    return "success" if status == "success" else "failed"
