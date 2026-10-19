# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded receipt evidence for the children of one authorized purchase request."""

import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_ORDERS = 10
STATUSES = {
    "completed_on_time",
    "completed_late",
    "open_overdue",
    "open_not_due",
    "unknown_schedule",
    "excluded_cancelled",
}


class RequestSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    identifier: str | None = Field(default=None, min_length=1, max_length=255)
    request_code: str | None = Field(default=None, min_length=1, max_length=255)
    request_id: UUID | None = None


async def request_reply(request, parent, *, client=None):
    """Use the declared PR selector once, rather than ask a child LLM to reinterpret it."""
    from flowbridge_agent.config import BackendConfig
    from flowbridge_agent.tools import failure, tool_result
    from flowbridge_agent.ver2_coordination import Invocation, Reply, collect, resolved_from, stamp
    from flowbridge_agent.ver2_data import DataBackend

    scope = Invocation(parent.run, "database", (*parent.path, "database"), request)

    def reply(status, **kwargs):
        return stamp(
            Reply(service="database", status=status, results=scope.receipts, **kwargs), scope
        )

    if parent.role != "central":
        return reply("error", warnings=["PROTOCOL_TASK_NOT_ALLOWED"])
    key = json.dumps([request.task, request.model_dump(mode="json")], sort_keys=True)
    if key in scope.run.replies:
        return scope.run.replies[key]
    if scope.run.a2a_calls >= scope.run.max_a2a:
        return reply("unavailable", warnings=["A2A_BUDGET_EXHAUSTED"])
    scope.run.a2a_calls += 1
    parent.delegations += 1
    scope.run.events.append({"source": "central", "target": "database", "task": request.task})
    try:
        selection = RequestSelection.model_validate(request.known)
        if (
            selection.identifier
            and selection.request_code
            and selection.identifier.casefold() != selection.request_code.casefold()
        ):
            raise ValueError("CONFLICTING_REQUEST_SELECTION")
    except (ValidationError, ValueError):
        return reply(
            "needs_input",
            warnings=["REQUEST_SELECTION_INVALID"],
            questions=["Bạn cho mã yêu cầu mua cần xem; công cụ này chưa nhận bộ lọc khác."],
        )
    identifier = (
        selection.identifier
        or selection.request_code
        or (str(selection.request_id) if selection.request_id else None)
    )
    client = client or DataBackend(BackendConfig.from_local())
    raw = await client.execute("purchase_get_request", {"identifier": identifier})
    if (
        selection.request_id
        and raw.get("request_id")
        and raw["request_id"] != str(selection.request_id)
    ):
        raw = failure("REQUEST_SELECTION_CHANGED")
    receipt = tool_result("purchase_get_request", {"identifier": identifier}, raw)
    collect(scope, receipt)
    state = receipt["status"]
    if state == "success" and raw.get("request_delivery_evidence", {}).get("complete") is not True:
        state = "partial"
    result = reply(
        state,
        resolved=resolved_from(scope.receipts),
        warnings=[raw["code"]] if raw.get("status") == "error" else [],
        questions=raw.get("questions", []),
    )
    scope.run.replies[key] = result
    return result


async def request_delivery(client, request: dict) -> dict:
    request_id = request["request_id"]
    result = {
        "request_id": request_id,
        "request_code": request["request_code"],
        "status": "unavailable",
        "scope": "caller_visible_orders_of_request",
        "orders": [],
        "complete": False,
        "summary": None,
        "warnings": [],
        "definition": "Tỷ lệ trễ thực tế = số đơn đã nhận đủ trễ / số đơn đã nhận đủ. "
        "Không có đơn nhận đủ thì tỷ lệ là null, không phải 0% hay xác suất giao trễ. "
        "Đơn chưa nhận đủ quá hạn được đếm riêng; không suy ngày nhận từ cam kết.",
    }

    def unavailable(code):
        result["warnings"].append(code)
        return result

    page = await client.request(
        "GET",
        "/api/v1/purchase-orders",
        params={"request_id": request_id, "page": 1, "page_size": MAX_ORDERS},
    )
    if page.get("status") == "error":
        return unavailable("REQUEST_ORDERS_UNAVAILABLE")
    try:
        total, headers = page["total"], page["items"]
        if (
            page["request_id"] != request_id
            or type(page["page"]) is not int
            or page["page"] != 1
            or type(page["page_size"]) is not int
            or page["page_size"] != MAX_ORDERS
            or type(total) is not int
            or total < 0
            or not isinstance(headers, list)
            or len(headers) != min(MAX_ORDERS, total)
        ):
            raise ValueError("INVALID_PARENT_FILTER")
        ids = set()
        for header in headers:
            order_id = str(UUID(header["purchase_order_id"]))
            if (
                order_id in ids
                or header["request_id"] != request_id
                or not isinstance(header["po_number"], str)
                or not header["po_number"].strip()
            ):
                raise ValueError("ORDER_PARENT_MISMATCH")
            UUID(header["supplier_id"])
            ids.add(order_id)
    except (KeyError, TypeError, ValueError, AttributeError):
        return unavailable("REQUEST_ORDER_FILTER_UNVERIFIED")
    result.update(total_orders=total, has_more=total > MAX_ORDERS)
    dates, counts = [], dict.fromkeys(STATUSES, 0)
    complete = total <= MAX_ORDERS
    for header in headers:
        child = {
            k: header.get(k) for k in ("purchase_order_id", "po_number", "supplier_id", "status")
        }
        if header["status"] in {"cancelled", "rejected"}:
            child["delivery_status"] = "excluded_cancelled"
            counts["excluded_cancelled"] += 1
        else:
            detail = await client.purchase_lookup("purchase-orders", header["purchase_order_id"])
            evidence = detail.get("delivery_evidence") or {}
            rows = (evidence.get("orders") or {}).get("items") or []
            verified = (
                detail.get("request_id") == request_id
                and detail.get("po_number") == header["po_number"]
                and detail.get("supplier_id") == header["supplier_id"]
                and detail.get("delivery_evidence_status") == "verified"
                and evidence.get("complete") is True
                and len(rows) == 1
                and rows[0].get("purchase_order_id") == header["purchase_order_id"]
                and rows[0].get("delivery_status") in STATUSES
                and bool(evidence.get("as_of"))
            )
            if verified:
                row = rows[0]
                child.update(
                    supplier_code=detail.get("supplier_code"),
                    supplier_name=detail.get("supplier_name"),
                    delivery_status=row["delivery_status"],
                    max_days_late=row["max_days_late"],
                    delivery_evidence=evidence,
                )
                counts[row["delivery_status"]] += 1
                dates.append(evidence["as_of"])
            else:
                child["delivery_status"] = "unavailable"
                complete = False
        result["orders"].append(child)
    result.update(
        status="verified" if complete else "partial",
        complete=complete,
        # Each PO is an authorized read snapshot; do not present multiple calls
        # as one atomic snapshot or as supplier-wide historical performance.
        observed_from=min(dates) if dates else None,
        observed_to=max(dates) if dates else None,
    )
    if complete:
        completed = counts["completed_on_time"] + counts["completed_late"]
        result["summary"] = {
            "count_unit": "đơn đặt hàng",
            "counts": counts,
            "completed_orders": completed,
            "late_completed_orders_percent": round(100 * counts["completed_late"] / completed, 2)
            if completed
            else None,
            "rate_scope": "Các đơn đã nhận đủ thuộc đúng yêu cầu này trong phạm vi quyền đọc; "
            "không phải tỷ lệ lịch sử của NCC hoặc xác suất dự đoán.",
        }
    else:
        result["warnings"].append("REQUEST_DELIVERY_INCOMPLETE_NO_GROUP_RATE")
    return result
