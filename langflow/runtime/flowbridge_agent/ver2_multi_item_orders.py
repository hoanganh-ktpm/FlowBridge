# SPDX-License-Identifier: GPL-3.0-or-later
"""Find real multi-product POs using a scoped SQL filter, without a child model."""

import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from flowbridge_agent.config import BackendConfig
from flowbridge_agent.tools import failure, tool_result
from flowbridge_agent.ver2_coordination import Invocation, Reply, collect, stamp
from flowbridge_agent.ver2_data import DataBackend


class MultiItemSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_distinct_products: int = Field(default=2, ge=2, le=100, strict=True)
    page: int = Field(default=1, ge=1, le=10000, strict=True)
    page_size: int = Field(default=1, ge=1, le=10, strict=True)


async def multi_item_reply(request, parent, *, client=None) -> dict:
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
        selection = MultiItemSelection.model_validate(request.known)
    except ValidationError:
        return reply(
            "needs_input",
            warnings=["MULTI_ITEM_SELECTION_INVALID"],
            questions=[
                "Bạn muốn tìm đơn có ít nhất mấy mặt hàng khác nhau? "
                "Công cụ này hỗ trợ số loại hàng và phân trang, chưa nhận bộ lọc khác."
            ],
        )
    params = selection.model_dump()
    client = client or DataBackend(BackendConfig.from_local())
    raw = await client.request("GET", "/api/v1/purchase-orders", params=params)
    action = "purchase_find_multi_item_orders"
    if raw.get("status") == "error":
        collect(scope, tool_result(action, params, raw))
        return reply("error", warnings=[raw.get("code", "BACKEND_FAILED")])
    try:
        rows, total = raw["items"], raw["total"]
        if (
            type(total) is not int
            or total < 0
            or not isinstance(rows, list)
            or any(
                type(raw.get(k)) is not int for k in ("page", "page_size", "min_distinct_products")
            )
            or raw.get("min_distinct_products") != selection.min_distinct_products
            or raw.get("page") != selection.page
            or raw.get("page_size") != selection.page_size
            or len(rows)
            != max(0, min(selection.page_size, total - (selection.page - 1) * selection.page_size))
        ):
            raise ValueError("INVALID_FILTERED_PAGE")
        seen = set()
        for row in rows:
            identifier = str(UUID(row["purchase_order_id"]))
            if (
                identifier in seen
                or not isinstance(row["po_number"], str)
                or not row["po_number"].strip()
            ):
                raise ValueError("INVALID_ORDER")
            seen.add(identifier)
            UUID(row["supplier_id"])
            if not isinstance(row["items"], list):
                raise ValueError("INVALID_LINES")
            distinct = {str(UUID(line["product_id"])) for line in row["items"]}
            if len(distinct) < selection.min_distinct_products:
                raise ValueError("FILTER_NOT_APPLIED")
    except (KeyError, ValueError, TypeError, AttributeError):
        collect(scope, tool_result(action, params, failure("INVALID_BACKEND_RESPONSE")))
        return reply("error", warnings=["INVALID_BACKEND_RESPONSE"])
    products, truncated, error = await client.catalog("products") if rows else ([], False, None)
    labels = {p["product_id"]: p for p in products} if not error else {}
    items, warnings = [], []
    for row in rows:
        lines = []
        for line in row["items"][:10]:
            label = labels.get(line["product_id"], {})
            lines.append(
                {
                    **{
                        k: line.get(k)
                        for k in ("product_id", "unit", "ordered_quantity", "unit_price")
                    },
                    **{k: label.get(k) for k in ("product_code", "product_name")},
                }
            )
        items.append(
            {
                **{
                    k: row.get(k)
                    for k in ("purchase_order_id", "po_number", "supplier_id", "status", "currency")
                },
                "distinct_product_count": len(
                    {str(UUID(line["product_id"])) for line in row["items"]}
                ),
                "items": lines,
                "items_truncated": len(row["items"]) > 10,
            }
        )
    if (
        error
        or truncated
        or any(not line["product_name"] for item in items for line in item["items"])
    ):
        warnings.append("PRODUCT_LABELS_INCOMPLETE")
    more = selection.page * selection.page_size < total
    partial = more or (total > 0 and not items) or any(i["items_truncated"] for i in items)
    status = "partial" if partial else "success" if items else "not_found"
    value = {
        "status": status,
        "items": items,
        "total_matches": total,
        "page": selection.page,
        "next_page": selection.page + 1 if more else None,
        "has_more": more,
        "min_distinct_products": selection.min_distinct_products,
        "scope": "caller_visible_orders",
        "warnings": warnings,
    }
    collect(scope, tool_result(action, params, value))
    result = reply(status, warnings=warnings)
    scope.run.replies[key] = result
    return result
