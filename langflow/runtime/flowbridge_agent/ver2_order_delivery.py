# SPDX-License-Identifier: GPL-3.0-or-later
"""Receipt evidence for one authorized PO; no supplier-wide page scanning."""

import json
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from flowbridge.supplier_assistant.delivery_statistics import DeliveryStatistics


async def order_delivery(client, order: dict) -> tuple[dict | None, str | None]:
    body = {
        "supplier_id": order["supplier_id"],
        "purchase_order_id": order["purchase_order_id"],
        "include_orders": True,
        "delivery_filter": "all",
        "page": 1,
        "page_size": 1,
    }
    result = await client.request("POST", "/internal/v1/tools/supplier-delivery-orders", body=body)
    if result.get("status") == "error":
        return None, "DELIVERY_EVIDENCE_UNAVAILABLE"
    try:
        stats = DeliveryStatistics.model_validate_json(json.dumps(result))
        page = stats.orders
        if (
            str(stats.supplier_id) != order["supplier_id"]
            or stats.scope not in {"caller_visible_orders", "company_procurement_reader"}
            or page is None
            or str(page.purchase_order_id) != order["purchase_order_id"]
            or page.delivery_filter != "all"
            or page.page != 1
            or page.page_size != 1
        ):
            raise ValueError("UNVERIFIED_ORDER_EVIDENCE")
        plans = {str(p["schedule_id"]): p for p in order.get("schedules", [])}
        for row in page.items:
            if row.po_number != order["po_number"]:
                raise ValueError("ORDER_CHANGED")
            for schedule in row.schedules:
                original = plans[str(schedule.schedule_id)]
                if str(schedule.requested_delivery_date) != original[
                    "requested_delivery_date"
                ] or Decimal(schedule.scheduled_quantity) != Decimal(
                    original["scheduled_quantity"]
                ):
                    raise ValueError("ORDER_CHANGED")
    except (ValidationError, ValueError, KeyError, TypeError, InvalidOperation):
        return None, "DELIVERY_EVIDENCE_INVALID_OR_ORDER_CHANGED"
    # This is evidence for ONE order. Do not expose its 100%/0% as a supplier's
    # historical late rate when embedding the result in the order lookup.
    evidence = stats.model_dump(
        mode="json",
        include={
            "supplier_id",
            "supplier_code",
            "supplier_name",
            "as_of",
            "scope",
            "orders",
            "complete",
            "warnings",
            "metric_definition",
        },
    )
    evidence["evidence_basis"] = "goods_receipts_by_original_schedule"
    return evidence, None
