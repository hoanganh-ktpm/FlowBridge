# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# UI labels and routing guidance. Validation remains in canonical API DTOs.
PARAMETER_HELP = {
    "query": "Cụm từ người dùng cần tìm; giữ tên/mã, không SQL, không token hay URL.",
    "limit": "Số kết quả tra cứu, 1–10; không phải số nhà cung cấp để dự đoán.",
    "product_id": ("UUID sản phẩm từ câu hỏi hoặc data.items[].product_id; không truyền tên."),
    "supplier_id": ("UUID nhà cung cấp từ câu hỏi/kết quả tra cứu; không truyền tên/mã hiển thị."),
    "identifier": "UUID hoặc mã hiển thị của đúng loại PR/PO đang được hỏi, không suy đoán UUID.",
    "quantity": 'Số lượng > 0, theo đơn vị sản phẩm; chuỗi decimal như "100", "2.5".',
    "requested_delivery_date": (
        "Ngày cần nhận YYYY-MM-DD, không phải ngày đặt; hỏi nếu thiếu/nhập nhằng."
    ),
    "top_k": "Số NCC/đoạn tài liệu yêu cầu; null dùng mặc định node. Không vượt giới hạn schema.",
    "ranking_policy": (
        "Chỉ risk_first: lọc điều kiện rồi ưu tiên rủi ro thấp; không xếp giá rẻ nhất."
    ),
    "currency": ("ISO ba chữ hoa VND/USD; null dùng mặc định, thường không lọc tiền tệ."),
    "max_unit_price": (
        "Trần giá MỘT đơn vị, chuỗi decimal >= 0; cần currency. Không phải tổng ngân sách. "
        "Chỉ lấy trần rõ ràng trong câu hỏi/mặc định node; không tự bịa giá."
    ),
    "order_id": "UUID PO; chỉ có mã PO thì lấy data.purchase_order_id qua node xem đơn hàng.",
    "prediction_id": (
        "UUID dự đoán đã lưu, không dùng UUID sản phẩm/PO; kịch bản tạm không có ID lưu."
    ),
}

TITLES = {
    "catalog_find_products": "Dữ liệu · Tìm sản phẩm",
    "catalog_find_suppliers": "Dữ liệu · Tìm nhà cung cấp",
    "catalog_get_supplier_offers": "Dữ liệu · Tra báo giá",
    "purchase_get_request": "Dữ liệu · Xem yêu cầu mua (PR)",
    "purchase_get_order": "Dữ liệu · Xem đơn đặt hàng (PO)",
    "prediction_rank_suppliers": "Dự đoán · Xếp hạng nhà cung cấp",
    "prediction_score_supplier": "Dự đoán · Chấm rủi ro nhà cung cấp",
    "prediction_get_order_risk": "Dự đoán · Xem rủi ro đơn hàng",
    "prediction_get_result": "Dự đoán · Đọc kết quả đã lưu",
    "knowledge_search_supplier": "Tri thức · Tìm bằng chứng tài liệu",
}

DESCRIPTIONS = {
    "catalog_find_products": (
        "Dùng để xác định sản phẩm khi mới biết tên/mã; đã có UUID tin cậy thì bỏ qua. "
        "Input query=tên/mã/UUID, limit mặc định 5. Output data.items chứa product_id, "
        "product_code, product_name, unit, product_category. Chỉ dùng ID khi status=success "
        "và chỉ một kết quả, truncated=false. ambiguous/truncated: hỏi chọn, không lấy dòng đầu. "
        "product_id và unit chuyển cho node báo giá hoặc dự đoán; không dự đoán trong node này."
    ),
    "catalog_find_suppliers": (
        "Dùng để xác định NCC khi mới biết tên/mã; không dùng để xếp hạng NCC. "
        "Input query, limit mặc định 5. Output data.items chứa supplier_id, supplier_code, "
        "supplier_name, city, country_of_origin. Chỉ chuyển supplier_id khi kết quả duy nhất, "
        "đầy đủ; ambiguous/truncated phải hỏi chọn. ID dùng cho báo giá, dự đoán hoặc tài liệu."
    ),
    "catalog_get_supplier_offers": (
        "Dùng khi hỏi giá hiện hành, MOQ, thời gian giao báo giá hoặc so giá. "
        "Cần product_id; supplier_id tùy chọn để lọc một NCC; null tìm NCC trong phạm vi dữ liệu. "
        "Output data.items: supplier_id, product_id, current_unit_price, currency, min_order_qty, "
        "quoted_lead_days, is_available. Không cần quantity/deadline chỉ để đọc giá. "
        "Không trả điểm ML; không xác nhận tồn kho thực tế. truncated=true không được khẳng định "
        "đã tìm giá rẻ nhất toàn hệ thống. Không gọi ML cho câu hỏi giá đơn thuần."
    ),
    "purchase_get_request": (
        "Đọc yêu cầu mua PR và các dòng hàng khi người dùng hỏi chi tiết/trạng thái PR. "
        "Input identifier=UUID hoặc mã PR. Output data.request_id, request_code, status, items. "
        "Có thể lấy product_id, quantity, requested_delivery_date từ đúng dòng PR để xếp hạng; "
        "nhiều dòng chưa rõ phải hỏi dòng nào. Không dùng mã PO hoặc tự dự đoán tất cả dòng."
    ),
    "purchase_get_order": (
        "Đọc đơn PO, các dòng hàng và lịch giao; input identifier=UUID hoặc mã PO. "
        "Output data.purchase_order_id, po_number, supplier_id, items và lịch giao. "
        "Khi hỏi rủi ro bằng mã PO: lấy purchase_order_id rồi gọi prediction_get_order_risk. "
        "Nếu đã có UUID PO và chỉ hỏi rủi ro, bỏ qua node này. Không tự tạo/duyệt/cập nhật PO."
    ),
    "prediction_rank_suppliers": (
        "Chọn top-k NCC ít rủi ro cho MỘT kịch bản chưa chọn NCC. Cần product_id, quantity, "
        "requested_delivery_date. top_k mặc định 3 (1–10); ranking_policy chỉ risk_first. "
        "Nếu câu hỏi có trần ĐƠN GIÁ, điền max_unit_price dạng chuỗi decimal và currency; "
        "không nêu trần thì dùng mặc định node, thường null, không suy ra từ chữ 'giá tốt/rẻ'. "
        "Output data.candidates kèm supplier_id, supplier_name, unit_price, currency, rank, "
        "result.risk_score và data.trace; data.unscored/excluded/warnings phải được đọc. "
        "Không chấm lại từng NCC đã có điểm. Không dùng thay đọc giá hoặc chấm NCC đã chọn."
    ),
    "prediction_score_supplier": (
        "Chấm rủi ro MỘT NCC đã chọn cho kịch bản mua cụ thể. Cần product_id, supplier_id, "
        "quantity, requested_delivery_date. Output data.scenario, data.result và data.trace. "
        "Không có tham số giá/top_k và không tìm NCC khác. Điểm trong kết quả ranking cùng "
        "kịch bản có thể dùng lại; không gọi chỉ để lấy lại điểm đó. null/insufficient_data "
        "là chưa đủ dữ liệu, không phải rủi ro bằng 0. Không lưu prediction mới."
    ),
    "prediction_get_order_risk": (
        "Đọc rủi ro hiện có cho PO và từng dòng, không tính/lưu lại. Input order_id=UUID PO. "
        "Output data.purchase_order_id, representative_risk_score, highest_risk_item_id, "
        "highest_risk_item_deadline, is_complete, items. Điểm đại diện là MAX dòng còn mở, "
        "không phải xác suất ít nhất một món trễ. Partial/insufficient_data/no_open_items "
        "phải nói rõ. Không dùng product_id, mã PO hay prediction_id thay order_id."
    ),
    "prediction_get_result": (
        "Đọc lại dự đoán ĐÃ LƯU theo prediction_id có thật, kèm dữ liệu nguồn/version nếu có. "
        "Không tạo dự đoán và không dùng cho kịch bản tạm chưa lưu. Giữ nguyên provenance "
        "trong data; không lấy một prediction lịch sử để kết luận trạng thái PO hiện tại "
        "khi chưa kiểm mốc dữ liệu. data.status=failed/snapshot_only chưa có điểm thành công. "
        "Nếu chưa biết prediction_id thì hỏi, không đoán."
    ),
    "knowledge_search_supplier": (
        "Tìm đánh giá, ghi chú hoặc bằng chứng định tính cần nguồn trích dẫn. Input query "
        "là câu hỏi tìm kiếm; supplier_id tùy chọn để giới hạn NCC; top_k mặc định 3, 1–20. "
        "Output data.chunks[].text, reference(document_id, document_version, chunk_id, "
        "source_uri, checksum), relevance_score và index_version/warnings. Chỉ trích từ chunks "
        "trả về; rỗng nghĩa là chưa tìm thấy nguồn, không chứng minh NCC không có vấn đề. "
        "Không thay API giá/ML. Chỉ có tool khi hệ thống cấp AI token và JobContext hợp lệ."
    ),
}

# Cross-node mappings are data paths in the actual returned envelope, not graph port links.
HANDOFFS = {
    "catalog_find_products": [
        "data.items[0].product_id -> product_id; chỉ khi success và duy nhất"
    ],
    "catalog_find_suppliers": [
        "data.items[0].supplier_id -> supplier_id; chỉ khi success và duy nhất"
    ],
    "catalog_get_supplier_offers": ["data.items[].supplier_id -> NCC trong bảng so giá"],
    "purchase_get_request": [
        "data.items[dòng đã chọn] -> product_id, quantity, requested_delivery_date"
    ],
    "purchase_get_order": ["data.purchase_order_id -> prediction_get_order_risk.order_id"],
    "prediction_rank_suppliers": [
        "data.candidates[].supplier_id -> knowledge_search_supplier.supplier_id",
        "data.candidates[].result -> tổng hợp điểm, không chấm lại",
    ],
    "prediction_score_supplier": [
        "data.scenario + data.result + data.trace -> câu trả lời có ngữ cảnh"
    ],
    "prediction_get_order_risk": ["data.items + điểm MAX + is_complete -> tổng hợp rủi ro PO"],
    "prediction_get_result": ["data -> kết quả lịch sử và mốc dữ liệu, không coi là điểm hiện tại"],
    "knowledge_search_supplier": ["data.chunks[].reference + text -> dẫn nguồn trong câu trả lời"],
}
