# SPDX-License-Identifier: GPL-3.0-or-later
"""Build presentation data from tool receipts, never from model prose or SQL."""

import json
import math
import re
from decimal import Decimal, InvalidOperation
from uuid import UUID

from flowbridge_agent.frontend_response_contract import Source, validate_response

# Allowlisted columns: unknown/nested/private tool fields never reach the frontend.
FIELDS = {
    "product_id": ("Mã định danh sản phẩm", "string", None),
    "product_code": ("Mã sản phẩm", "string", None),
    "product_name": ("Sản phẩm", "string", None),
    "supplier_id": ("Mã định danh nhà cung cấp", "string", None),
    "supplier_code": ("Mã nhà cung cấp", "string", None),
    "supplier_name": ("Nhà cung cấp", "string", None),
    "unit": ("Đơn vị", "string", None),
    "currency": ("Tiền tệ", "string", None),
    "current_unit_price": ("Đơn giá", "decimal", "price"),
    "unit_price": ("Đơn giá", "decimal", "price"),
    "quantity": ("Số lượng", "decimal", "quantity"),
    "ordered_quantity": ("Số lượng đặt", "decimal", "quantity"),
    "quoted_lead_days": ("Thời gian giao dự kiến", "integer", "ngày"),
    "rank": ("Hạng", "integer", "hạng"),
    "risk_score": ("Điểm rủi ro (chưa hiệu chuẩn)", "number", "điểm"),
    "hist_sample_count": ("Số dòng lịch sử dùng tính đặc trưng", "integer", "dòng"),
    "order_date": ("Ngày phát hành đơn", "date", None),
    "lead_days": ("Thời gian nhận đủ", "integer", "ngày"),
    "delivered_late": ("Đã nhận đủ trễ hạn", "boolean", None),
    "highest_item_risk_score": ("Điểm dòng rủi ro cao nhất", "number", "điểm"),
    "scored_value_coverage": ("Tỷ phần giá trị đã chấm", "number", None),
    "high_risk_item_count": ("Số dòng có điểm cao", "integer", "dòng"),
    "unscored_item_count": ("Số dòng chưa chấm được", "integer", "dòng"),
    "aggregation_policy": ("Quy tắc tổng hợp", "string", None),
    "score_status": ("Trạng thái chấm điểm", "string", None),
    "status": ("Trạng thái", "string", None),
    "prediction_id": ("Mã kết quả", "string", None),
    "request_code": ("Mã phiếu yêu cầu mua", "string", None),
    "po_number": ("Mã đơn đặt hàng", "string", None),
    "distinct_product_count": ("Số loại hàng khác nhau trong đơn", "integer", "loại hàng"),
    "distinct_supplier_count": ("Số NCC của yêu cầu mua", "integer", "nhà cung cấp"),
    "total_amount": ("Tổng tiền đơn", "decimal", "currency"),
    "requested_delivery_date": ("Ngày cần nhận", "date", None),
    "completion_date": ("Ngày nhận đủ lịch giao", "date", None),
    "days_late": ("Số ngày trễ của lịch giao", "integer", "ngày"),
    "max_days_late": ("Số ngày trễ lớn nhất của đơn", "integer", "ngày"),
    "received_quantity": ("Lượng đã nhận", "decimal", "quantity"),
    "scheduled_quantity": ("Lượng theo lịch giao", "decimal", "quantity"),
    "schedule_id": ("Định danh lịch giao", "string", None),
    "delivery_status": ("Tình trạng giao của đơn", "string", None),
    "is_available": ("Có trong danh mục cung ứng", "boolean", None),
    "missing_fields": ("Dữ liệu còn thiếu", "string", None),
    "text": ("Đoạn trích", "string", None),
    "source_id": ("Nguồn", "string", None),
}
TOOLS = {
    "catalog_list": "Danh mục",
    "catalog_read_offers": "Báo giá nhà cung cấp",
    "catalog_resolve_entities": "Đối tượng đã xác minh",
    "purchase_get_request": "Phiếu yêu cầu mua hàng",
    "purchase_get_order": "Đơn đặt hàng",
    "purchase_find_multi_item_orders": "Đơn có nhiều mặt hàng",
    "purchase_find_multi_supplier_requests": "Yêu cầu mua chia cho nhiều nhà cung cấp",
    "supplier_delivery_orders": "Đơn và lịch giao của nhà cung cấp",
    "ml_feature_history": "Lịch sử dùng tính đặc trưng ML",
    "prediction_rank_suppliers": "Xếp hạng nhà cung cấp",
    "prediction_score_supplier": "Đánh giá rủi ro nhà cung cấp",
    "prediction_get_order_risk": "Rủi ro đơn đặt hàng",
    "prediction_get_result": "Kết quả dự đoán",
    "knowledge_search_supplier": "Bằng chứng tài liệu",
}


def cell(value, kind):
    if value is None:
        return None
    if kind in {"string", "date"}:
        if not isinstance(value, str) or len(value) > 6000:
            raise ValueError("INVALID_TEXT_CELL")
        if kind == "date":
            from datetime import date

            date.fromisoformat(value)
        return value
    if kind == "boolean" and type(value) is bool:
        return value
    if kind == "integer" and type(value) is int:
        return value
    if kind == "number" and type(value) in {int, float} and math.isfinite(value):
        return value
    if kind == "decimal" and not isinstance(value, (bool, float)):
        try:
            number = Decimal(value)
            if number.is_finite() and len(number.as_tuple().digits) <= 24:
                return format(number, "f")
        except (InvalidOperation, TypeError, ValueError):
            pass
    raise ValueError("INVALID_NUMERIC_CELL")


def build_response(
    answer: str,
    receipts: list[dict],
    *,
    run_id=None,
    issues: list[str] | None = None,
    questions: list[str] | None = None,
) -> dict:
    response = {
        "schema_version": "1.0",
        "status": "completed",
        "run_id": run_id,
        "answer": answer[:24000],
        "datasets": [],
        "tables": [],
        "charts": [],
        "sources": [],
        "warnings": [],
        "questions": (questions or [])[:20],
        "error": None,
    }
    failed, incomplete = False, False

    def warn(code, message, dataset_id=None):
        value = {"code": code, "message": message, "dataset_id": dataset_id}
        if value not in response["warnings"] and len(response["warnings"]) < 100:
            response["warnings"].append(value)

    for code in issues or []:
        warn(code, "Một phần kết quả công cụ chưa hợp lệ hoặc chưa có dữ liệu nguồn.")
        incomplete = True

    seen = set()
    if len(receipts) > 20:
        warn("RECEIPT_LIMIT", "Chỉ hiển thị tối đa 20 kết quả công cụ trong một phản hồi.")
        incomplete = True
    for receipt in receipts[:20]:
        encoded = json.dumps(receipt, sort_keys=True, ensure_ascii=False, default=str)
        if encoded in seen:
            continue
        seen.add(encoded)
        tool = receipt.get("tool")
        if tool not in TOOLS:
            warn("UNSUPPORTED_TOOL", "Chưa có quy tắc trình bày cho công cụ này.")
            incomplete = True
            continue
        data = receipt.get("data")
        if receipt.get("error") or not isinstance(data, dict):
            failed = True
            warn("TOOL_FAILED", f"Chưa lấy được kết quả: {TOOLS[tool]}.")
            error = receipt.get("error") or {}
            code = error.get("code") if isinstance(error, dict) else None
            if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code):
                warn(code, f"Mã lỗi công cụ: {code}.")
            continue
        collections = [data.get(key, []) for key in ("items", "chunks", "candidates", "unscored")]
        valid_shape = all(
            value is None
            or (isinstance(value, list) and all(isinstance(row, dict) for row in value))
            for value in collections
        )
        valid_shape &= isinstance(data.get("resolved") or {}, dict)
        valid_shape &= isinstance(data.get("result") or {}, dict)
        risk_rows = [data.get("result") or {}]
        if valid_shape:
            risk_rows += [
                (r.get("result") or {})
                for key in ("candidates", "unscored")
                for r in data.get(key) or []
            ]
            for risk in risk_rows:
                valid_shape &= isinstance(risk, dict)
                if isinstance(risk, dict):
                    missing = risk.get("missing_fields") or []
                    valid_shape &= isinstance(missing, list) and all(
                        isinstance(s, str) for s in missing
                    )
        if not valid_shape:
            warn("INVALID_TOOL_DATA", f"Kết quả {TOOLS[tool]} không đúng cấu trúc hỗ trợ.")
            incomplete = True
            continue
        identifier = f"dataset_{len(response['datasets']) + 1}"
        source = {"id": f"source_{len(response['sources']) + 1}", "kind": "tool", "tool": tool}
        response["sources"].append(source)
        source_ids = [source["id"]]
        # Only business context returned by the tool, not caller/model-authored input.
        resolved = data.get("resolved") or {}
        context = {
            "product_id": resolved.get("product_id"),
            "unit": resolved.get("unit"),
            "currency": None,
            "complete": False,
        }
        rows = []
        if tool == "knowledge_search_supplier":
            for chunk in (data.get("chunks") or [])[:100]:
                if len(response["sources"]) >= 240:
                    warn(
                        "SOURCE_LIMIT",
                        "Giới hạn số nguồn; chỉ hiển thị phần kết quả đã nhận.",
                        identifier,
                    )
                    incomplete = True
                    break
                ref = chunk.get("reference") or {}
                try:
                    if not isinstance(ref, dict):
                        raise ValueError
                    for key in ("document_id", "chunk_id"):
                        UUID(ref[key])
                    version = ref["document_version"]
                    if not isinstance(version, str) or len(version) > 100:
                        raise ValueError
                    uri = ref["source_uri"]
                    prefix = (
                        "core-document" if uri.startswith("core-document://") else "local-document"
                    )
                    expected_prefix = f"{prefix}://{ref['document_id']}/{version}#"
                    if not uri.startswith(expected_prefix) or not re.fullmatch(
                        r"(?:core|local)-document://[^\s?#]+#(?:bytes|chars)=\d+:\d+", uri
                    ):
                        raise ValueError
                    span = uri.split("=", 1)[1].split(":")
                    if int(span[0]) >= int(span[1]):
                        raise ValueError
                    text = cell(chunk.get("text"), "string")
                    if not text:
                        raise ValueError
                except (ValueError, KeyError, TypeError, AttributeError):
                    warn(
                        "INVALID_CITATION",
                        "Đoạn trích không có nguồn hợp lệ đã được bỏ qua.",
                        identifier,
                    )
                    incomplete = True
                    continue
                doc_source = {
                    "id": f"source_{len(response['sources']) + 1}",
                    "kind": "document",
                    "tool": tool,
                    **{
                        k: ref.get(k)
                        for k in (
                            "document_id",
                            "document_version",
                            "chunk_id",
                            "source_uri",
                            "checksum",
                        )
                    },
                }
                try:
                    Source.model_validate(doc_source)
                except ValueError:
                    warn(
                        "INVALID_CITATION",
                        "Đoạn trích có tham chiếu không hợp lệ đã được bỏ qua.",
                        identifier,
                    )
                    incomplete = True
                    continue
                response["sources"].append(doc_source)
                source_ids.append(doc_source["id"])
                rows.append({"text": text, "source_id": doc_source["id"]})
        elif tool == "prediction_rank_suppliers":
            for candidate in [*(data.get("candidates") or []), *(data.get("unscored") or [])]:
                risk = candidate.get("result") or {}
                rows.append(
                    {
                        **candidate,
                        "risk_score": risk.get("risk_score"),
                        "score_status": risk.get("status"),
                        "missing_fields": ", ".join(risk.get("missing_fields") or []),
                        "hist_sample_count": risk.get("hist_sample_count"),
                    }
                )
        elif tool == "prediction_score_supplier":
            risk = data.get("result") or {}
            rows = [
                {
                    **{k: data.get(k) for k in ("supplier_id", "supplier_name", "product_id")},
                    "risk_score": risk.get("risk_score"),
                    "score_status": risk.get("status"),
                    "missing_fields": ", ".join(risk.get("missing_fields") or []),
                    "hist_sample_count": risk.get("hist_sample_count"),
                }
            ]
            context["history_reference"] = risk.get("history_reference")
            incomplete |= risk.get("status") != "success"
        elif tool == "prediction_get_order_risk":
            rows = [
                {
                    "risk_score": data.get("representative_risk_score"),
                    "status": data.get("status"),
                    **{
                        k: data.get(k)
                        for k in (
                            "aggregation_policy",
                            "highest_item_risk_score",
                            "scored_value_coverage",
                            "high_risk_item_count",
                            "unscored_item_count",
                        )
                    },
                }
            ]
            weighted = data.get("aggregation_policy") == "remaining_value_weighted_v2"
            warn(
                "ORDER_RISK_VALUE_WEIGHTED" if weighted else "ORDER_RISK_MAX",
                "Điểm đại diện theo giá trị hàng còn chưa nhận; MAX là cảnh báo riêng. "
                "Không phải xác suất hoặc tỷ lệ trễ."
                if weighted
                else "Điểm đại diện là MAX theo API, không phải tỷ lệ giao trễ.",
                identifier,
            )
        elif tool == "purchase_get_request" and isinstance(
            data.get("request_delivery_evidence"), dict
        ):
            group = data["request_delivery_evidence"]
            children = group.get("orders")
            if (
                group.get("request_id") != data.get("request_id")
                or group.get("request_code") != data.get("request_code")
                or not isinstance(children, list)
                or any(not isinstance(o, dict) for o in children)
                or len({o.get("purchase_order_id") for o in children}) != len(children)
            ):
                warn("INVALID_TOOL_DATA", "Bằng chứng không khớp yêu cầu mua.", identifier)
                incomplete = True
                continue
            context.update(
                as_of=group.get("observed_to"),
                scope=group.get("scope"),
                count_unit="đơn đặt hàng",
                total_matches=group.get("total_orders"),
                metric_definition=group.get("definition"),
                denominator_definition="Chỉ các đơn đã nhận đủ thuộc đúng yêu cầu này.",
            )
            for order in children:
                status = order.get("delivery_status")
                rows.append(
                    {
                        **order,
                        "request_code": data.get("request_code"),
                        "delivered_late": (status == "completed_late")
                        if status in {"completed_on_time", "completed_late"}
                        else None,
                    }
                )
            if group.get("status") == "unavailable":
                # Keep the original request lines when receipt service is down.
                rows = data.get("items") or []
            data = {**data, "truncated": group.get("complete") is not True}
        elif tool == "supplier_delivery_orders" or (
            tool == "purchase_get_order" and data.get("delivery_evidence_status") == "verified"
        ):
            delivery = data if tool == "supplier_delivery_orders" else data.get("delivery_evidence")
            if not isinstance(delivery, dict):
                warn("INVALID_TOOL_DATA", "Thiếu bằng chứng phiếu nhận của đơn.", identifier)
                incomplete = True
                continue
            page = delivery.get("orders") or {}
            if (
                not isinstance(page, dict)
                or not isinstance(page.get("items"), list)
                or any(
                    not isinstance(o, dict)
                    or not isinstance(o.get("po_number"), str)
                    or not isinstance(o.get("delivery_status"), str)
                    or not isinstance(o.get("schedules"), list)
                    or any(not isinstance(p, dict) for p in o.get("schedules", []))
                    for o in page.get("items", [])
                )
            ):
                warn("INVALID_TOOL_DATA", "Thiếu danh sách đơn được kiểm chứng.", identifier)
                incomplete = True
                continue
            if tool == "purchase_get_order" and (
                delivery.get("supplier_id") != data.get("supplier_id")
                or page.get("purchase_order_id") != data.get("purchase_order_id")
                or any(
                    row.get("purchase_order_id") != data.get("purchase_order_id")
                    or row.get("po_number") != data.get("po_number")
                    for row in page["items"]
                )
            ):
                warn("INVALID_TOOL_DATA", "Bằng chứng phiếu nhận không khớp đơn.", identifier)
                incomplete = True
                continue
            context.update(
                {
                    "as_of": delivery.get("as_of"),
                    "count_unit": "lịch giao",
                    "metric_definition": delivery.get("metric_definition"),
                    "scope": delivery.get("scope"),
                    "page": page.get("page"),
                    "total_matches": page.get("total_matches"),
                    "next_page": page.get("next_page"),
                }
            )
            for order in page["items"]:
                for plan in order.get("schedules", []):
                    rows.append(
                        {
                            **plan,
                            "supplier_code": delivery.get("supplier_code"),
                            "supplier_name": delivery.get("supplier_name"),
                            "po_number": order["po_number"],
                            "delivery_status": {
                                "completed_on_time": "Đã nhận đủ đúng hạn",
                                "completed_late": "Đã nhận đủ trễ hạn",
                                "open_overdue": "Chưa nhận đủ, đang quá hạn",
                                "open_not_due": "Phần còn thiếu chưa đến hạn",
                                "unknown_schedule": "Chưa đủ lịch giao để phân loại",
                            }.get(order["delivery_status"], order["delivery_status"]),
                        }
                    )
                incomplete |= bool(order.get("details_truncated"))
            data = {
                **data,
                "has_more": page.get("has_more"),
                "truncated": any(
                    o.get("details_truncated")
                    or any(p.get("receipts_truncated") for p in o.get("schedules", []))
                    for o in page["items"]
                ),
            }
        elif tool == "purchase_find_multi_supplier_requests":
            if any(
                not isinstance(group.get("purchase_orders"), list)
                or any(not isinstance(order, dict) for order in group["purchase_orders"])
                for group in data.get("items", [])
            ):
                warn("INVALID_TOOL_DATA", "Nhóm nhà cung cấp không đúng cấu trúc.", identifier)
                incomplete = True
                continue
            context.update(
                {
                    "scope": data.get("scope"),
                    "page": data.get("page"),
                    "total_matches": data.get("total_matches"),
                    "next_page": data.get("next_page"),
                    "count_unit": "đơn đặt hàng",
                    "metric_definition": "Các đơn cùng một yêu cầu mua; mỗi đơn một NCC. "
                    "Không gộp khác yêu cầu.",
                }
            )
            for group in data.get("items", []):
                for order in group.get("purchase_orders", []):
                    rows.append(
                        {
                            **order,
                            "request_code": group.get("request_code"),
                            "distinct_supplier_count": group.get("distinct_supplier_count"),
                        }
                    )
        elif tool == "purchase_find_multi_item_orders":
            context.update(
                {
                    "scope": data.get("scope"),
                    "page": data.get("page"),
                    "total_matches": data.get("total_matches"),
                    "next_page": data.get("next_page"),
                    "count_unit": "dòng hàng",
                    "metric_definition": "Lọc số product_id khác nhau trước phân trang; "
                    "không cộng khác đơn vị.",
                }
            )
            for order in data.get("items", []):
                for line in order.get("items", []):
                    rows.append(
                        {
                            **line,
                            **{
                                k: order.get(k)
                                for k in (
                                    "po_number",
                                    "currency",
                                    "distinct_product_count",
                                )
                            },
                        }
                    )
                incomplete |= bool(order.get("items_truncated"))
        elif tool == "ml_feature_history":
            rows = data.get("items") or []
            reference = data.get("reference") or {}
            context.update(
                {
                    "history_reference": reference,
                    "as_of": reference.get("feature_asof"),
                    "reference_matches": data.get("reference_matches"),
                    "expected_count_matches": data.get("expected_count_matches"),
                    "scope": data.get("scope"),
                    "page": data.get("page"),
                    "total_matches": data.get("total_matches"),
                    "next_page": data.get("next_page"),
                    "metric_definition": data.get("definition"),
                    "count_unit": "dòng hàng",
                }
            )
            data = {**data, "has_more": data.get("next_page") is not None}
            if data.get("reference_matches") is not True:
                warn(
                    "HISTORY_NOT_VERIFIED_ORIGINAL",
                    "Danh sách tái dựng từ database; "
                    "chưa xác minh khớp tham chiếu gốc của lần chấm trước.",
                    identifier,
                )
        elif tool == "prediction_get_result":
            rows = [data]
        elif tool == "catalog_resolve_entities":
            rows = [resolved] if resolved else []
        else:
            rows = data.get("items") or []

        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            warn("INVALID_TOOL_ROWS", "Dữ liệu bảng không đúng cấu trúc.", identifier)
            incomplete = True
            continue
        truncated = len(rows) > 200 or bool(data.get("truncated") or data.get("has_more"))
        rows = rows[:200]
        if truncated:
            warn(
                "INCOMPLETE_DATA",
                "Chỉ hiển thị phần dữ liệu đã nhận; không đại diện toàn bộ.",
                identifier,
            )
        incomplete |= truncated or receipt.get("status") in {
            "partial",
            "truncated",
            "insufficient_data",
        }
        if receipt.get("status") in {"ambiguous", "needs_input", "needs_service"}:
            response["status"] = "needs_input"
        if tool.startswith("prediction_"):
            warn(
                "RISK_NOT_PROBABILITY",
                "Điểm rủi ro chưa hiệu chuẩn thành xác suất hay tỷ lệ giao trễ.",
                identifier,
            )
            context["metric_definition"] = "Điểm rủi ro từ API ML, chưa hiệu chuẩn thành xác suất."
        if tool == "knowledge_search_supplier":
            warn(
                "RETRIEVED_EXCERPTS_ONLY",
                "Đoạn trích dùng làm bằng chứng; "
                "chưa có số liệu cấu trúc để vẽ thống kê giao hàng.",
                identifier,
            )
        if tool == "catalog_read_offers":
            warn("QUOTE_NOT_STOCK", "Báo giá không xác nhận lượng tồn kho thực tế.", identifier)
        if data.get("warnings"):
            warn(
                "UPSTREAM_WARNINGS",
                "Công cụ có cảnh báo về phạm vi hoặc giới hạn dữ liệu.",
                identifier,
            )
            incomplete = True
        if not rows:
            warn("NO_DATA", "Không có dòng dữ liệu phù hợp để vẽ biểu đồ.")
            continue

        for key in ("product_id", "unit", "currency"):
            values = {r.get(key) for r in rows if isinstance(r.get(key), str) and r[key]}
            if len(values) == 1 and all(r.get(key) for r in rows):
                context[key] = next(iter(values))
            elif any(key in r for r in rows):
                context[key] = None
        context["complete"] = (
            not truncated
            and tool != "knowledge_search_supplier"
            and receipt.get("status") not in {"partial", "insufficient_data", "truncated"}
        )
        columns, clean = [], [{} for _ in rows]
        for field, (label, kind, unit) in FIELDS.items():
            if not any(field in r for r in rows):
                continue
            for raw, target in zip(rows, clean, strict=True):
                try:
                    target[field] = cell(raw.get(field), kind)
                except (ValueError, TypeError, OverflowError):
                    target[field] = None
                    warn(
                        "INVALID_VALUE",
                        f"Giá trị không hợp lệ tại cột {label}; giữ null.",
                        identifier,
                    )
                    incomplete = True
            if unit == "price":
                unit = (
                    f"{context['currency']}/{context['unit']}"
                    if context.get("currency") and context.get("unit")
                    else None
                )
            elif unit == "quantity":
                unit = context.get("unit")
            elif unit == "currency":
                unit = context.get("currency")
            columns.append(
                {"field": field, "label": label, "type": kind, "unit": unit, "nullable": True}
            )
        if not columns:
            warn("UNSUPPORTED_DATA", "Chưa có cột dữ liệu được hỗ trợ cho kết quả này.")
            incomplete = True
            continue
        dataset = {
            "id": identifier,
            "title": TOOLS[tool],
            "columns": columns,
            "rows": clean,
            "context": context,
            "source_ids": source_ids,
        }
        response["datasets"].append(dataset)
        response["tables"].append(
            {
                "id": "table_" + identifier,
                "title": TOOLS[tool],
                "dataset_id": identifier,
                "fields": [c["field"] for c in columns],
            }
        )
        comparable = all(context.get(k) for k in ("product_id", "unit", "currency"))
        label_field = next(
            (
                k
                for k in ("supplier_name", "supplier_code", "supplier_id")
                if all(r.get(k) is not None for r in clean)
            ),
            None,
        )
        if label_field and len({r[label_field] for r in clean}) == len(clean):
            price = next(
                (k for k in ("current_unit_price", "unit_price") if any(k in r for r in clean)),
                None,
            )
            for field, title, unit in [
                (price, "So sánh đơn giá", f"{context.get('currency')}/{context.get('unit')}"),
                ("risk_score", "Điểm rủi ro nhà cung cấp (chưa hiệu chuẩn)", "điểm"),
            ]:
                if field is None or not all(r.get(field) is not None for r in clean):
                    continue
                if not comparable:
                    warn(
                        "NOT_COMPARABLE",
                        "Thiếu hoặc khác sản phẩm, đơn vị, tiền tệ; chưa dựng biểu đồ so sánh.",
                        identifier,
                    )
                    continue
                if field == "risk_score" and any(r.get("score_status") != "success" for r in clean):
                    continue
                response["charts"].append(
                    {
                        "id": f"chart_{identifier}_{field}",
                        "title": title,
                        "dataset_id": identifier,
                        "type": "bar",
                        "stacking": "none",
                        "x_field": label_field,
                        "x_label": "Nhà cung cấp",
                        "y_label": title,
                        "y_unit": unit,
                        "series": [{"field": field, "label": title}],
                    }
                )

    usable = bool(response["datasets"])
    if failed:
        response["status"] = "partial" if usable else "failed"
        response["error"] = {
            "code": "TOOL_FAILED",
            "message": "Một hoặc nhiều công cụ chưa trả được kết quả.",
            "retryable": False,
        }
    elif response["status"] != "needs_input" and incomplete:
        response["status"] = "partial"
    if not receipts and not issues:
        warn("NO_STRUCTURED_DATA", "Lượt này không có kết quả công cụ để dựng bảng hoặc biểu đồ.")
    if not response["answer"]:
        response["answer"] = (
            "Kết quả từ công cụ được trình bày trong bảng và biểu đồ."
            if usable
            else "Chưa có dữ liệu để trình bày."
        )
    dataset_ids = {d["id"] for d in response["datasets"]}
    for warning in response["warnings"]:
        if warning["dataset_id"] not in dataset_ids:
            warning["dataset_id"] = None
    if len(json.dumps(response, ensure_ascii=False).encode("utf-8")) > 1_048_576:
        return validate_response(
            {
                **response,
                "status": "failed",
                "answer": "Kết quả vượt giới hạn phản hồi.",
                "datasets": [],
                "tables": [],
                "charts": [],
                "sources": [],
                "warnings": [],
                "error": {
                    "code": "RESPONSE_TOO_LARGE",
                    "message": "Thu hẹp phạm vi dữ liệu và thử lại.",
                    "retryable": False,
                },
            }
        )
    return validate_response(response)
