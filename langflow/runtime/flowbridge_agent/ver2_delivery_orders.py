# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded factual delivery lookup behind the existing Data protocol adapter."""

import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from flowbridge.supplier_assistant.delivery_statistics import DeliveryStatistics
from flowbridge_agent.config import BackendConfig
from flowbridge_agent.tools import tool_result
from flowbridge_agent.ver2_coordination import Invocation, Reply, collect, stamp
from flowbridge_agent.ver2_data import DataBackend


class DeliverySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier: str | None = Field(default=None, min_length=1, max_length=255)
    supplier_id: UUID | None = None
    supplier_code: str | None = None
    supplier_name: str | None = None
    delivery_filter: Literal["late", "completed_late", "open_overdue", "all"] = "late"
    page: int = Field(default=1, ge=1, le=10000, strict=True)
    page_size: int = Field(default=5, ge=1, le=10, strict=True)

    @model_validator(mode="after")
    def selector(self):
        if not any((self.supplier, self.supplier_id, self.supplier_code, self.supplier_name)):
            raise ValueError("SUPPLIER_REQUIRED")
        return self


async def delivery_reply(request, parent, *, client=None) -> dict:
    scope = Invocation(parent.run, "database", (*parent.path, "database"), request)

    def reply(status, **kwargs):
        return stamp(
            Reply(service="database", status=status, results=scope.receipts, **kwargs), scope
        )

    if parent.role != "central":
        return reply("error", warnings=["PROTOCOL_TASK_NOT_ALLOWED"])
    key = json.dumps(["delivery_orders", request.model_dump(mode="json")], sort_keys=True)
    if key in scope.run.replies:
        return scope.run.replies[key]
    if scope.run.a2a_calls >= scope.run.max_a2a:
        return reply("unavailable", warnings=["A2A_BUDGET_EXHAUSTED"])
    scope.run.a2a_calls += 1
    parent.delegations += 1
    scope.run.events.append({"source": "central", "target": "database", "task": request.task})
    try:
        selection = DeliverySelection.model_validate(
            {k: v for k, v in request.known.items() if v is not None}
        )
    except ValidationError:
        return reply(
            "needs_input",
            missing=["supplier_or_supported_filters"],
            questions=[
                "Bạn muốn xem nhà cung cấp nào? Công cụ hỗ trợ tình trạng giao và phân trang; "
                "chưa hỗ trợ lọc thêm theo kỳ hoặc sản phẩm."
            ],
            warnings=["DELIVERY_SELECTION_INVALID"],
        )
    client = client or DataBackend(BackendConfig.from_local())
    selector = (
        selection.supplier
        or selection.supplier_code
        or selection.supplier_name
        or str(selection.supplier_id)
    )
    identity = await client.resolve(None, selector)
    receipt = tool_result("catalog_resolve_entities", {"supplier": selector}, identity)
    collect(scope, receipt)
    if identity.get("status") != "success":
        status = identity.get("status", "error")
        return reply(
            status if status in {"ambiguous", "not_found", "error"} else "partial",
            missing=identity.get("missing", []),
            questions=identity.get("questions", []),
            warnings=identity.get("warnings", []),
        )
    resolved = identity["resolved"]
    supplier_id = resolved["supplier_id"]
    if selection.supplier_id and str(selection.supplier_id) != supplier_id:
        return reply(
            "needs_input",
            warnings=["SUPPLIER_SELECTION_MISMATCH"],
            questions=["Tên/mã và định danh nhà cung cấp chưa khớp; bạn chọn lại NCC nhé."],
        )
    body = {
        "supplier_id": supplier_id,
        "include_orders": True,
        "delivery_filter": selection.delivery_filter,
        "page": selection.page,
        "page_size": selection.page_size,
    }
    result = await client.request(
        "POST", "/internal/v1/tools/supplier-delivery-orders", body=body
    )
    if result.get("status") == "error":
        collect(scope, tool_result("supplier_delivery_orders", body, result))
        return reply("error", resolved=resolved, warnings=[result.get("code", "BACKEND_FAILED")])
    try:
        statistics = DeliveryStatistics.model_validate_json(json.dumps(result))
        page = statistics.orders
        if (
            str(statistics.supplier_id) != supplier_id
            or statistics.scope not in {"caller_visible_orders", "company_procurement_reader"}
            or page is None
            or page.page != selection.page
            or page.page_size != selection.page_size
            or page.delivery_filter != selection.delivery_filter
        ):
            raise ValueError("INVALID_DELIVERY_RESPONSE")
    except (ValidationError, ValueError, TypeError):
        return reply("error", resolved=resolved, warnings=["INVALID_BACKEND_RESPONSE"])
    partial = (
        page.has_more
        or not statistics.complete
        or any(
            row.details_truncated or any(s.receipts_truncated for s in row.schedules)
            for row in page.items
        )
    )
    status = "partial" if partial else "success" if page.items else "not_found"
    result = {**statistics.model_dump(mode="json"), "status": status}
    collect(scope, tool_result("supplier_delivery_orders", body, result))
    value = reply(
        status,
        resolved=resolved,
        warnings=statistics.warnings + (["DELIVERY_ORDERS_PAGE_HAS_MORE"] if page.has_more else []),
    )
    scope.run.replies[key] = value
    return value
