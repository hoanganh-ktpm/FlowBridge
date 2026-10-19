# SPDX-License-Identifier: GPL-3.0-or-later
"""Find one real PR split across suppliers; never invent a multi-supplier PO."""

import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from flowbridge_agent.config import BackendConfig
from flowbridge_agent.tools import failure, tool_result
from flowbridge_agent.ver2_coordination import Invocation, Reply, collect, stamp
from flowbridge_agent.ver2_data import DataBackend


class MultiSupplierSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    min_distinct_suppliers: int = Field(default=2, ge=2, le=100, strict=True)
    page: int = Field(default=1, ge=1, le=10000, strict=True)
    page_size: int = Field(default=1, ge=1, le=10, strict=True)
    request_code: str | None = Field(default=None, min_length=1, max_length=50)


async def multi_supplier_reply(request, parent, *, client=None):
    scope = Invocation(parent.run, "database", (*parent.path, "database"), request)

    def reply(status, **kw):
        return stamp(Reply(service="database", status=status, results=scope.receipts, **kw), scope)

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
        selection = MultiSupplierSelection.model_validate(request.known)
    except ValidationError:
        return reply(
            "needs_input",
            warnings=["MULTI_SUPPLIER_SELECTION_INVALID"],
            questions=[
                "Bạn muốn tìm yêu cầu được tách cho ít nhất mấy nhà cung cấp? "
                "Công cụ nhận số NCC, mã yêu cầu và phân trang; chưa nhận bộ lọc khác."
            ],
        )
    params = selection.model_dump(exclude_none=True)
    if selection.request_code:
        params["request_code"] = selection.request_code.upper()
    client = client or DataBackend(BackendConfig.from_local())
    raw = await client.request("GET", "/api/v1/purchase-requests", params=params)
    action = "purchase_find_multi_supplier_requests"
    if raw.get("status") == "error":
        collect(scope, tool_result(action, params, raw))
        return reply("error", warnings=[raw.get("code", "BACKEND_FAILED")])
    try:
        total, rows = raw["total"], raw["items"]
        if (
            type(total) is not int
            or total < 0
            or not isinstance(rows, list)
            or any(
                type(raw.get(k)) is not int for k in ("page", "page_size", "min_distinct_suppliers")
            )
            or raw["page"] != selection.page
            or raw["page_size"] != selection.page_size
            or raw["min_distinct_suppliers"] != selection.min_distinct_suppliers
            or len(rows)
            != max(0, min(selection.page_size, total - (selection.page - 1) * selection.page_size))
        ):
            raise ValueError("INVALID_GROUP_PAGE")
        seen_requests, seen_orders = set(), set()
        for row in rows:
            request_id = str(UUID(row["request_id"]))
            if (
                request_id in seen_requests
                or not isinstance(row["request_code"], str)
                or not row["request_code"].strip()
            ):
                raise ValueError("INVALID_REQUEST")
            if (
                selection.request_code
                and row["request_code"].upper() != selection.request_code.upper()
            ):
                raise ValueError("REQUEST_FILTER_IGNORED")
            seen_requests.add(request_id)
            orders = row["purchase_orders"]
            if not isinstance(orders, list) or not 2 <= len(orders) <= 100:
                raise ValueError("INVALID_ORDERS")
            suppliers = set()
            for order in orders:
                order_id = str(UUID(order["purchase_order_id"]))
                if (
                    order_id in seen_orders
                    or order["request_id"] != request_id
                    or order["status"] in {"cancelled", "rejected"}
                    or not isinstance(order["po_number"], str)
                    or not order["po_number"].strip()
                ):
                    raise ValueError("ORDER_GROUP_MISMATCH")
                seen_orders.add(order_id)
                suppliers.add(str(UUID(order["supplier_id"])))
            if (
                type(row["distinct_supplier_count"]) is not int
                or row["distinct_supplier_count"] != len(suppliers)
                or len(suppliers) < selection.min_distinct_suppliers
            ):
                raise ValueError("SUPPLIER_FILTER_NOT_APPLIED")
    except (KeyError, TypeError, ValueError, AttributeError):
        collect(scope, tool_result(action, params, failure("INVALID_BACKEND_RESPONSE")))
        return reply("error", warnings=["INVALID_BACKEND_RESPONSE"])
    suppliers, truncated, error = await client.catalog("suppliers") if rows else ([], False, None)
    names = {s["supplier_id"]: s for s in suppliers} if not error else {}
    groups, missing = [], False
    for row in rows:
        orders = []
        for order in row["purchase_orders"]:
            label = names.get(order["supplier_id"], {})
            missing |= not bool(label)
            orders.append(
                {
                    **{
                        k: order.get(k)
                        for k in (
                            "purchase_order_id",
                            "po_number",
                            "supplier_id",
                            "status",
                            "currency",
                            "total_amount",
                        )
                    },
                    **{k: label.get(k) for k in ("supplier_code", "supplier_name")},
                }
            )
        groups.append(
            {
                **{k: row[k] for k in ("request_id", "request_code", "distinct_supplier_count")},
                "purchase_orders": orders,
            }
        )
    warnings = ["SUPPLIER_LABELS_INCOMPLETE"] if error or truncated or missing else []
    more = selection.page * selection.page_size < total
    status = (
        "partial" if more or (total > 0 and not groups) else "success" if groups else "not_found"
    )
    data = {
        "status": status,
        "items": groups,
        "total_matches": total,
        "page": selection.page,
        "next_page": selection.page + 1 if more else None,
        "has_more": more,
        "min_distinct_suppliers": selection.min_distinct_suppliers,
        "scope": "caller_visible_requests",
        "warnings": warnings,
        "structure": "Một yêu cầu mua được tách thành nhiều đơn; mỗi đơn thuộc một NCC.",
    }
    collect(scope, tool_result(action, params, data))
    result = reply(status, warnings=warnings)
    scope.run.replies[key] = result
    return result
