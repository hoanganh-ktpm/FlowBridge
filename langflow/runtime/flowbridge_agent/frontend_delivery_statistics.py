# SPDX-License-Identifier: GPL-3.0-or-later
"""Fetch authenticated database counts and add chartable views, without model calls."""

import asyncio
from datetime import timedelta, timezone
from uuid import UUID

import httpx

from flowbridge.supplier_assistant.delivery_statistics import DeliveryStatistics
from flowbridge_agent.config import BackendConfig
from flowbridge_agent.frontend_response_contract import validate_response

STATUS_LABELS = {
    "completed_on_time": "Đã nhận đủ đúng hạn",
    "completed_late": "Đã nhận đủ trễ hạn",
    "open_overdue": "Chưa nhận đủ, đang quá hạn",
    "open_not_due": "Chưa nhận đủ, phần còn lại chưa quá hạn",
    "unknown_schedule": "Chưa đủ dữ liệu lịch giao",
}


def supplier_ids(receipts):
    if not any(r.get("tool") == "knowledge_search_supplier" for r in receipts):
        return []
    found = set()
    for receipt in receipts:
        data = receipt.get("data") or {}
        if not isinstance(data, dict):
            continue
        values = []
        if receipt.get("tool") == "catalog_resolve_entities" and receipt.get("status") == "success":
            resolved = data.get("resolved") or {}
            if isinstance(resolved, dict):
                values.append(resolved.get("supplier_id"))
        if receipt.get("tool") == "knowledge_search_supplier":
            chunks = data.get("chunks") or []
            if isinstance(chunks, list):
                values.extend(
                    c["reference"].get("supplier_id")
                    for c in chunks[:100]
                    if isinstance(c, dict) and isinstance(c.get("reference"), dict)
                )
        for value in values:
            try:
                found.add(str(UUID(value)))
            except (ValueError, TypeError, AttributeError):
                pass
    return sorted(found)


def append_statistics(response: dict, data: DeliveryStatistics) -> dict:
    response["warnings"] = [w for w in response["warnings"] if w["code"] != "NO_STRUCTURED_DATA"]
    suffix = str(data.supplier_id)
    source = f"delivery_source_{suffix}"
    counts_id, summary_id = f"delivery_counts_{suffix}", f"delivery_summary_{suffix}"
    unit = data.count_unit
    context = {
        "complete": data.complete,
        "count_unit": unit,
        "as_of": data.as_of.isoformat(),
        "period_from": data.issued_from.isoformat(),
        "period_to": data.issued_to.isoformat(),
        "metric_definition": data.metric_definition,
        "denominator_definition": data.denominator_definition,
    }
    response["sources"].append(
        {
            "id": source,
            "kind": "tool",
            "tool": "supplier_delivery_statistics",
            "source_uri": f"flowbridge-api://supplier-delivery-statistics/{suffix}"
            f"#as_of={data.as_of.isoformat()}",
        }
    )
    columns = [
        {"field": "delivery_status", "label": "Tình trạng giao hàng", "type": "string"},
        {"field": "order_count", "label": "Số đơn đặt hàng", "type": "integer", "unit": unit},
    ]
    rows = [
        {"delivery_status": label, "order_count": getattr(data.counts, field)}
        for field, label in STATUS_LABELS.items()
    ]
    response["datasets"].append(
        {
            "id": counts_id,
            "title": "Tiến độ giao hàng — " + data.supplier_name,
            "columns": columns,
            "rows": rows,
            "context": context,
            "source_ids": [source],
        }
    )
    response["tables"].append(
        {
            "id": "table_" + counts_id,
            "title": "Số đơn theo tình trạng",
            "dataset_id": counts_id,
            "fields": [c["field"] for c in columns],
        }
    )
    if data.total_eligible_orders:
        response["charts"].append(
            {
                "id": "chart_" + counts_id,
                "title": "Tiến độ giao hàng — " + data.supplier_name,
                "dataset_id": counts_id,
                "type": "bar",
                "orientation": "horizontal",
                "stacking": "none",
                "x_field": "delivery_status",
                "x_label": "Tình trạng",
                "y_label": "Số đơn đặt hàng",
                "y_unit": unit,
                "series": [{"field": "order_count", "label": "Số đơn đặt hàng"}],
            }
        )
    summary = {
        "supplier_name": data.supplier_name,
        "completed_orders": data.completed_orders,
        "late_orders": data.counts.completed_late,
        "late_rate_percent": data.late_rate_percent,
        "partially_received_open_orders": data.counts.partial_open,
        "excluded_cancelled_orders": data.counts.excluded_cancelled,
    }
    summary_columns = [
        {"field": "supplier_name", "label": "Nhà cung cấp", "type": "string"},
        {
            "field": "completed_orders",
            "label": "Tổng đơn đã nhận đủ",
            "type": "integer",
            "unit": unit,
        },
        {"field": "late_orders", "label": "Đơn nhận đủ trễ hạn", "type": "integer", "unit": unit},
        {
            "field": "late_rate_percent",
            "label": "Tỷ lệ trễ trên đơn đã nhận đủ",
            "type": "number",
            "unit": "%",
        },
        {
            "field": "partially_received_open_orders",
            "label": "Đơn mở đã nhận một phần (tập con)",
            "type": "integer",
            "unit": unit,
        },
        {
            "field": "excluded_cancelled_orders",
            "label": "Đơn hủy/từ chối bị loại",
            "type": "integer",
            "unit": unit,
        },
    ]
    response["datasets"].append(
        {
            "id": summary_id,
            "title": "Mẫu số và tỷ lệ trễ",
            "columns": summary_columns,
            "rows": [summary],
            "context": context,
            "source_ids": [source],
        }
    )
    response["tables"].append(
        {
            "id": "table_" + summary_id,
            "title": "Mẫu số và tỷ lệ trễ",
            "dataset_id": summary_id,
            "fields": list(summary),
        }
    )
    for code in data.warnings:
        response["warnings"].append(
            {
                "code": code,
                "dataset_id": counts_id,
                "message": "Trạng thái hủy/từ chối dùng giá trị hiện tại; "
                "chưa tái dựng lịch sử thay đổi trạng thái."
                if code == "CANCELLATION_STATUS_IS_CURRENT_NOT_HISTORIZED"
                else "Một số đơn chưa đủ lịch giao để phân loại.",
            }
        )
    when = data.as_of.astimezone(timezone(timedelta(hours=7))).strftime(
        "%d/%m/%Y %H:%M (UTC+07:00)"
    )
    rate = "chưa có mẫu số" if data.late_rate_percent is None else f"{data.late_rate_percent:.2f}%"
    prefix = (
        "### Số liệu dùng cho bảng và biểu đồ — database\n\n"
        f"{data.supplier_name}, chốt tại {when}: {data.completed_orders} đơn đã nhận đủ, "
        f"{data.counts.completed_late} đơn nhận đủ trễ; tỷ lệ trễ {rate}. "
        f"Có {data.counts.open_overdue} đơn chưa nhận đủ đang quá hạn và "
        f"{data.counts.open_not_due} đơn chưa nhận đủ có phần còn lại chưa quá hạn. "
        "Phạm vi: lịch sử đơn của nhà cung cấp trong hệ thống, theo quyền AI_SERVICE. "
        "Đơn nhận một phần là tập con của đơn mở, không cộng thành nhóm riêng.\n\n"
        "### Nhận xét từ tài liệu\n\n"
    )
    response["answer"] = (prefix + response["answer"])[:24000]
    if response["status"] == "failed":
        response["status"] = "partial"
    if not data.complete and response["status"] == "completed":
        response["status"] = "partial"
    return validate_response(response)


async def enrich_delivery_statistics(response: dict, receipts: list[dict]) -> dict:
    identifiers = supplier_ids(receipts)
    if not identifiers:
        return response
    try:
        config = BackendConfig.from_local()
    except ValueError:
        return unavailable(response)
    if not config.ai_token or not config.job_context:
        return unavailable(response)

    async def fetch(identifier):
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as http:
            result = await http.post(
                config.origin + "/internal/v1/tools/supplier-delivery-statistics",
                headers={
                    "Authorization": "Bearer " + config.ai_token,
                    "X-Job-Context": config.job_context,
                },
                json={"supplier_id": identifier},
            )
            result.raise_for_status()
            data = DeliveryStatistics.model_validate_json(result.content)
            if str(data.supplier_id) != identifier:
                raise ValueError("STATISTICS_SUPPLIER_MISMATCH")
            return data

    results = await asyncio.gather(*(fetch(i) for i in identifiers[:3]), return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            response = unavailable(response)
        else:
            response = append_statistics(response, result)
    if len(identifiers) > 3:
        response = unavailable(response)
    return response


def unavailable(response):
    warning = {
        "code": "DELIVERY_STATISTICS_UNAVAILABLE",
        "dataset_id": None,
        "message": "Chưa lấy được thống kê database đã xác thực; không suy số từ câu trả lời.",
    }
    if warning not in response["warnings"]:
        response["warnings"].append(warning)
    if response["status"] == "completed":
        response["status"] = "partial"
    return validate_response(response)
