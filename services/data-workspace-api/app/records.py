# SPDX-License-Identifier: GPL-3.0-or-later
"""Paged current-org registers through Core; no fixture/SQL or cross-org fallback."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, model_validator

from .schemas import DTO, ProductRef

DEFINITIONS = {
    "warehouses": (
        "warehouse_code",
        ["warehouse_id", "warehouse_code", "warehouse_name", "status"],
    ),
    "products": ("sku", ["product_id", "sku", "product_name", "uom", "status"]),
    "orders": (
        "order_number",
        [
            "order_id",
            "order_number",
            "status",
            "warehouse_id",
            "created_at",
            "confirmed_at",
            "requested_handoff_at",
            "completed_at",
            "cancelled_at",
            "line_count",
        ],
    ),
    "order_lines": (
        None,
        ["order_id", "order_line_id", "line_no", "sku", "product_name", "uom", "ordered_quantity"],
    ),
    "inventory": (
        "sku",
        [
            "warehouse_id",
            "sku",
            "uom",
            "on_hand_quantity",
            "reserved_quantity",
            "available_quantity",
        ],
    ),
}


class RecordsRequest(DTO):
    collection: Literal["warehouses", "products", "orders", "order_lines", "inventory"]
    offset: int = Field(default=0, ge=0, le=10000, strict=True)
    code: str | None = Field(default=None, min_length=1, max_length=80)
    status: str | None = Field(default=None, pattern=r"^[A-Z_]{1,30}$")
    order_id: UUID | None = None
    warehouse_id: UUID | None = None

    @model_validator(mode="after")
    def filters(self):
        if self.collection == "order_lines":
            if (
                self.order_id is None
                or self.code is not None
                or self.status is not None
                or self.warehouse_id is not None
            ):
                raise ValueError("Order lines require one order identity and no header filters")
        elif self.order_id is not None:
            raise ValueError("Order identity is only supported for line details")
        if self.warehouse_id is not None and self.collection not in {"orders", "inventory"}:
            raise ValueError("Warehouse scope is only supported for orders and inventory")
        if self.collection == "inventory" and self.status is not None:
            raise ValueError("Inventory balances do not have a catalog status")
        if self.code is not None:
            self.code = self.code.strip()
            if not self.code:
                raise ValueError("Code must not be blank")
        return self


async def records_page(body, ctx, connector, source_names):
    role = "stock" if body.collection == "inventory" else "records_" + body.collection
    catalog = await connector.catalog(ctx)
    matches = [p for p in catalog if p["name"] == source_names[role]]
    if len(matches) != 1:
        raise HTTPException(
            403,
            detail={
                "code": "REGISTER_NOT_AUTHORIZED",
                "message": "Nguồn tra cứu chưa được công bố hoặc tài khoản chưa có quyền đọc. "
                "Liên hệ IT; không thay bằng dữ liệu mẫu.",
            },
        )
    product = matches[0]
    ref = ProductRef(
        data_product_id=product["id"], contract_version_id=product["contract_version_id"]
    )
    code_field, fields = DEFINITIONS[body.collection]
    columns = sorted(set(fields) | set(product["business_key"]) | {"org_id"})
    if not set(columns) <= {f["name"] for f in product["fields"]}:
        raise HTTPException(403, "Register fields are not fully granted")
    where = {}
    if body.code is not None:
        where[code_field] = {"_eq": body.code.strip()}
    if body.status is not None:
        where["status"] = {"_eq": body.status}
    if body.order_id is not None:
        where["order_id"] = {"_eq": str(body.order_id)}
    if body.warehouse_id is not None:
        where["warehouse_id"] = {"_eq": str(body.warehouse_id)}
    # Look ahead one row. Never download the complete history for each UI page.
    page = await connector.query(ctx, ref, columns, 51, body.offset, where)
    rows = page.get("rows")
    if (
        not isinstance(rows, list)
        or len(rows) > 51
        or any(not isinstance(r, dict) or not set(columns) <= r.keys() for r in rows)
        or page.get("has_more") is True
        and len(rows) < 51
    ):
        raise HTTPException(502, "Invalid register page")
    keys = [tuple(str(r.get(k)) for k in product["business_key"]) for r in rows]
    if (
        len(keys) != len(set(keys))
        or any(str(r.get("org_id")) != str(ctx.org_id) for r in rows)
        or any(r.get(k) is None for r in rows for k in product["business_key"])
    ):
        raise HTTPException(502, "Foreign organization or duplicate register row")
    if body.warehouse_id is not None and any(
        str(row.get("warehouse_id")) != str(body.warehouse_id) for row in rows
    ):
        raise HTTPException(502, "Register escaped requested warehouse")
    has_more = len(rows) > 50
    if body.offset + 50 > 10000 and has_more:
        raise HTTPException(
            422,
            detail={
                "code": "REGISTER_READ_LIMIT",
                "message": "Danh sách vượt giới hạn phân trang. Lọc theo mã hoặc trạng thái; "
                "chưa trả danh sách thiếu như thể đã đầy đủ.",
            },
        )
    # Names are bounded to the returned page, using the caller's catalog grants.
    # Missing inventory records remain absent, never manufactured as zero stock.
    rows = [dict(row) for row in rows[:50]]
    if rows and body.collection in {"inventory", "orders"}:
        warehouses = await lookup_names(
            ctx,
            connector,
            catalog,
            source_names["records_warehouses"],
            "warehouse_id",
            {str(r["warehouse_id"]) for r in rows if r.get("warehouse_id")},
            ["warehouse_code", "warehouse_name"],
        )
        for row in rows:
            row.update(warehouses.get(str(row.get("warehouse_id")), {}))
        if body.collection == "inventory":
            products = await lookup_names(
                ctx,
                connector,
                catalog,
                source_names["records_products"],
                "sku",
                {str(r["sku"]) for r in rows},
                ["product_name", "uom"],
            )
            for row in rows:
                product_name = products.get(str(row["sku"]), {})
                if product_name and product_name.get("uom") != row["uom"]:
                    raise HTTPException(502, "Inventory/catalog unit mismatch")
                row["product_name"] = product_name.get("product_name")
    await connector.check(ctx, ref, columns)
    return {
        "items": rows,
        "offset": body.offset,
        "page_size": 50,
        "has_more": has_more,
        "read_at": datetime.now(UTC).isoformat(),
        "scope": "current_organization",
        "source": {"name": product["name"], "ref": ref.model_dump(mode="json")},
    }


async def lookup_names(ctx, connector, catalog, name, key, identities, fields):
    if not identities:
        return {}
    matches = [p for p in catalog if p["name"] == name]
    if len(matches) != 1:
        raise HTTPException(403, "Catalog names are not authorized")
    product = matches[0]
    columns = sorted({"org_id", key, *fields, *product["business_key"]})
    if not set(columns) <= {f["name"] for f in product["fields"]}:
        raise HTTPException(403, "Catalog name fields are not granted")
    ref = ProductRef(
        data_product_id=product["id"], contract_version_id=product["contract_version_id"]
    )
    page = await connector.query(ctx, ref, columns, 51, 0, {key: {"_in": sorted(identities)}})
    rows = page.get("rows")
    if not isinstance(rows, list) or len(rows) > len(identities) or page.get("has_more"):
        raise HTTPException(502, "Invalid name lookup page")
    result = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not set(columns) <= row.keys()
            or str(row.get("org_id")) != str(ctx.org_id)
            or str(row.get(key)) not in identities
            or str(row[key]) in result
        ):
            raise HTTPException(502, "Invalid or duplicate catalog identity")
        result[str(row[key])] = {field: row[field] for field in fields}
    await connector.check(ctx, ref, columns)
    return result
