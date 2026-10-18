# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

"""Read-only catalog adapters for ver2; names/codes stay outside canonical ML DTOs."""

import json
import re
from difflib import SequenceMatcher
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, ValidationError, model_validator

from flowbridge_agent.tools import BackendTools, ToolInput, failure, normalized

Selector = Annotated[str, Field(min_length=1, max_length=255)]


class ResolveInput(ToolInput):
    product: Selector | None = None
    supplier: Selector | None = None

    @model_validator(mode="after")
    def has_selector(self):
        if not self.product and not self.supplier:
            raise ValueError("Provide a product or supplier name/code")
        return self


class CatalogInput(ToolInput):
    resource: Literal["products", "suppliers"]
    query: Selector | None = None
    page: Annotated[int, Field(ge=1, le=10000, strict=True)] = 1
    page_size: Annotated[int, Field(ge=1, le=20, strict=True)] = 10


class QuoteInput(ToolInput):
    product: Selector
    supplier: Selector | None = None


class PurchaseLookupInput(ToolInput):
    identifier: Selector | None = Field(
        default=None,
        description="Mã đầy đủ/một phần, UUID hoặc từ khóa trong ghi chú/ngày/trạng thái; "
        "null để xem các đơn/phiếu trong phạm vi được cấp quyền",
    )


DATA_SPECS = {
    "catalog_resolve_entities": (
        ResolveInput,
        "Dữ liệu · Nhận diện sản phẩm và NCC",
        "Xác định product và supplier cùng MỘT lần bằng tên tự nhiên, mã hoặc UUID. "
        "Ví dụ product='băng keo Scotch 371 vàng', supplier='Nhà vật tư Phúc Lâm'. "
        "Có thể chỉ truyền một đối tượng. Tự đọc API danh mục, chuẩn hóa dấu/khoảng trắng/"
        "quy cách, trả data.resolved gồm UUID thật và mã/tên/unit; data.entities có các lựa "
        "chọn khi nhập nhằng. Chỉ ID trong resolved được dùng tiếp. Không yêu cầu người dùng "
        "biết UUID. Không tự chọn gợi ý gần đúng hoặc đổi màu/quy cách. Không dự đoán ML.",
    ),
    "catalog_list": (
        CatalogInput,
        "Dữ liệu · Duyệt danh mục",
        "Liệt kê sản phẩm hoặc NCC qua API danh sách. resource=products/suppliers; query=null "
        "để duyệt tất cả, hoặc cụm tên/mã để lọc; page/page_size cho trang tiếp theo. "
        "Trả items có UUID/mã/tên/unit hoặc địa phương; total_matches, has_more, next_page, "
        "truncated. Một danh sách không đồng nghĩa người dùng đã chọn dòng đầu. "
        "Muốn biết ai bán MỘT sản phẩm/giá/MOQ hãy dùng catalog_read_offers.",
    ),
    "catalog_read_offers": (
        QuoteInput,
        "Dữ liệu · Báo giá theo tên hoặc mã",
        "Đọc NCC bán sản phẩm, giá, currency, MOQ, quoted_lead_days và is_available. "
        "product nhận TÊN/MÃ/UUID; supplier tùy chọn cùng định dạng. Tự resolve bên trong, "
        "không cần gọi resolve trước chỉ để xem giá. Trả data.resolved và items kèm mã/tên "
        "sản phẩm/NCC. Không cần số lượng/ngày nhận. Không xác nhận tồn kho thực tế, không "
        "tính rủi ro. Đọc truncated trước khi kết luận rẻ nhất hoặc toàn bộ NCC.",
    ),
    "purchase_get_request": (
        PurchaseLookupInput,
        "Dữ liệu · Tìm và đọc yêu cầu mua (PR)",
        "Tìm PR theo mã đầy đủ/một phần hoặc từ khóa trong ghi chú/ngày/trạng thái; "
        "identifier=null để xem lựa chọn. Chỉ đọc các PR mà người dùng có quyền. "
        "Nhiều khớp trả items và questions để chọn, không lấy dòng đầu. Một khớp mới "
        "đọc chi tiết request_id, request_code, items. Không cần UUID. Nếu nhiều dòng "
        "hàng, hỏi chọn dòng trước ML; không mặc định preferred_supplier_id.",
    ),
    "purchase_get_order": (
        PurchaseLookupInput,
        "Dữ liệu · Tìm và đọc đơn đặt hàng (PO)",
        "Tìm PO theo mã đầy đủ/một phần hoặc ngày/trạng thái; identifier=null để xem "
        "lựa chọn. Chỉ đọc phạm vi người dùng có quyền. Nhiều khớp trả items và questions "
        "để chọn; một khớp đọc chi tiết purchase_order_id, po_number, items, schedules. "
        "Không cần UUID. Tra mô tả không có trong danh sách: hỏi mã/ngày để thu hẹp, "
        "không khẳng định không có đơn. Không tạo/sửa đơn và không tự dự đoán.",
    ),
}


def words(value: str) -> list[str]:
    # 48mm × 100m == 48 mm x 100 m; accents/case do not change identity.
    value = normalized(value).replace("×", " x ")
    return re.findall(r"[a-z]+|\d+(?:[.,]\d+)?", value)


def selector_words(value: str, stem: str) -> list[str]:
    value = normalized(value)
    prefixes = (
        r"^(?:(?:nha cung cap|nha|cong ty|tnhh|co phan)\s+)+"
        if stem == "supplier"
        else r"^(?:(?:san pham|mat hang|loai)\s+)+"
    )
    return [w for w in words(re.sub(prefixes, "", value)) if w != "x"]


def compact(row: dict, stem: str) -> dict:
    fields = [stem + "_id", stem + "_code", stem + "_name"]
    fields += (
        ["unit", "product_category"]
        if stem == "product"
        else ["city", "country_of_origin", "status"]
    )
    return {key: row.get(key) for key in fields}


def valid_identities(rows: list[dict], resource: str) -> bool:
    stems = (
        ("product", "supplier")
        if resource == "supplier-products"
        else ("product" if resource == "products" else "supplier",)
    )
    try:
        for row in rows:
            for stem in stems:
                UUID(row[stem + "_id"])
                if resource != "supplier-products" and any(
                    not isinstance(row.get(stem + suffix), str) or not row[stem + suffix].strip()
                    for suffix in ("_code", "_name")
                ):
                    return False
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
    return True


def match_entity(rows: list[dict], selector: str, stem: str, truncated: bool) -> dict:
    key = normalized(selector)
    fields = [stem + suffix for suffix in ("_id", "_code", "_name")]
    exact = [r for r in rows if any(normalized(str(r.get(f, ""))) == key for f in fields)]
    query = selector_words(selector, stem)
    # A name can contain a secondary display code, e.g. "Phúc Lâm — NCC-06".
    matches = exact or [
        r for r in rows if query and set(query) <= set(words(str(r.get(fields[2], ""))))
    ]
    # Code formatting (DEMO P03 / DEMO-P03) is an exact normalized code match.
    code_matches = [r for r in rows if query and query == words(str(r.get(fields[1], "")))]
    if not exact and code_matches:
        matches = code_matches
    status = (
        "truncated"
        if truncated
        else ("success" if len(matches) == 1 else "ambiguous" if matches else "not_found")
    )
    suggestions = []
    if not matches:
        # Approximation is only a choice list, never an automatic identity decision.
        ranked = sorted(
            (
                (
                    SequenceMatcher(
                        None, " ".join(query), " ".join(words(str(r.get(fields[2], ""))))
                    ).ratio(),
                    r,
                )
                for r in rows
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        suggestions = [compact(r, stem) for score, r in ranked[:5] if score >= 0.45]
    items = [compact(r, stem) for r in matches[:10]]
    return {
        "status": status,
        "query": selector,
        "items": items,
        "match_count": len(matches),
        "truncated": truncated or len(matches) > 10,
        "suggestions": suggestions,
    }


class DataBackend(BackendTools):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Request/graph instance only; no identities or credentials shared between users.
        self.catalogs = {}

    async def catalog(self, resource: str):
        if resource not in self.catalogs:
            rows, truncated, error = await self.pages("/api/v1/" + resource)
            if error is None and not valid_identities(rows, resource):
                error = failure("INVALID_BACKEND_RESPONSE")
            self.catalogs[resource] = (rows, truncated, error)
        return self.catalogs[resource]

    async def resolve(self, product: str | None = None, supplier: str | None = None) -> dict:
        entities, resolved, missing, questions = {}, {}, [], []
        for stem, selector in (("product", product), ("supplier", supplier)):
            if selector is None:
                continue
            rows, truncated, error = await self.catalog(stem + "s")
            if error:
                return error
            result = match_entity(rows, selector, stem, truncated)
            entities[stem] = result
            if result["status"] == "success" and not result["truncated"]:
                resolved.update(result["items"][0])
            else:
                missing.append(stem)
                options = result["items"] or result["suggestions"]
                choices = "; ".join(
                    f"{r[stem + '_code']}: {r[stem + '_name']}" for r in options[:5]
                )
                label = "sản phẩm" if stem == "product" else "nhà cung cấp"
                questions.append(
                    f"Bạn muốn {label} nào: {choices}?"
                    if choices
                    else f"Bạn cho thêm tên hoặc mã {label} cần tìm nhé."
                )
        states = {r["status"] for r in entities.values()}
        status = (
            "success"
            if not missing
            else (
                "partial"
                if resolved
                else "ambiguous"
                if "ambiguous" in states
                else "partial"
                if "truncated" in states
                else "not_found"
            )
        )
        return {
            "status": status,
            "resolved": resolved,
            "entities": entities,
            "missing": missing,
            "questions": questions,
            "truncated": any(r["truncated"] for r in entities.values()),
            "warnings": ["Catalog scan incomplete; unresolved identities need a narrower scope"]
            if "truncated" in states
            else [],
        }

    async def browse(self, resource: str, query: str | None, page: int, page_size: int) -> dict:
        stem = "product" if resource == "products" else "supplier"
        if query is None:
            result = await self.request(
                "GET", "/api/v1/" + resource, params={"page": page, "page_size": page_size}
            )
            if result.get("status") == "error":
                return result
            rows, total = result.get("items"), result.get("total")
            if (
                not isinstance(rows, list)
                or type(total) is not int
                or total < 0
                or any(not isinstance(r, dict) for r in rows)
                or len(rows) > page_size
                or not valid_identities(rows, resource)
            ):
                return failure("INVALID_BACKEND_RESPONSE")
            truncated, has_more = False, page * page_size < total
        else:
            rows, truncated, error = await self.catalog(resource)
            if error:
                return error
            tokens = selector_words(query, stem)
            if not tokens:
                return failure("INVALID_TOOL_ARGUMENTS")
            rows = [
                r
                for r in rows
                if set(tokens)
                <= set(words(str(r.get(stem + "_name", "")) + " " + str(r.get(stem + "_code", ""))))
            ]
            total = len(rows)
            has_more = page * page_size < total
            rows = rows[(page - 1) * page_size : page * page_size]
        return {
            "status": "partial" if truncated else "success" if rows else "not_found",
            "resource": resource,
            "items": [compact(r, stem) for r in rows],
            "page": page,
            "page_size": page_size,
            "total_matches": None if truncated else total,
            "matched_count_at_least": total,
            "has_more": has_more,
            "next_page": page + 1 if has_more else None,
            "truncated": truncated,
            "warnings": ["Filtered catalog scan incomplete"] if truncated else [],
        }

    async def quotes(self, product: str, supplier: str | None) -> dict:
        identity = await self.resolve(product, supplier)
        if identity["status"] != "success":
            return identity
        resolved = identity["resolved"]
        rows, truncated, error = await self.catalog("supplier-products")
        if error:
            return error
        rows = [
            r
            for r in rows
            if str(r.get("product_id")) == resolved["product_id"]
            and (supplier is None or str(r.get("supplier_id")) == resolved["supplier_id"])
        ]
        # Read names once for the returned quote table, rather than N supplier lookups.
        suppliers, names_truncated, error = await self.catalog("suppliers")
        if error:
            return error
        names = {str(r["supplier_id"]): r for r in suppliers}
        items = []
        for row in rows[:20]:
            item = {
                **row,
                **{k: v for k, v in resolved.items() if k.startswith("product_") or k == "unit"},
            }
            vendor = names.get(str(row.get("supplier_id")), {})
            item.update({k: vendor.get(k) for k in ("supplier_name", "supplier_code")})
            items.append(item)
        incomplete = truncated or len(rows) > 20
        return {
            "status": "partial" if incomplete else "success" if items else "not_found",
            "resolved": resolved,
            "items": items,
            "truncated": incomplete,
            "warnings": ["Quotes are not confirmation of actual stock or available quantity"]
            + (["Supplier names scan incomplete"] if names_truncated else []),
        }

    async def purchase_lookup(self, kind: str, identifier: str | None) -> dict:
        path = "/api/v1/" + kind
        key, code, label = (
            ("request_id", "request_code", "phiếu mua PR")
            if kind == "purchase-requests"
            else ("purchase_order_id", "po_number", "đơn hàng PO")
        )
        selected = None
        if identifier:
            try:
                selected = str(UUID(identifier))
            except ValueError:
                pass
        if selected is None:
            rows, truncated, error = await self.pages(path)
            if error:
                return error
            try:
                for row in rows:
                    UUID(row[key])
                    if not isinstance(row[code], str) or not row[code].strip():
                        return failure("INVALID_BACKEND_RESPONSE")
            except (KeyError, ValueError, TypeError, AttributeError):
                return failure("INVALID_BACKEND_RESPONSE")
            if identifier:
                exact = [r for r in rows if words(r[code]) == words(identifier)]
                tokens = set(
                    words(
                        re.sub(
                            r"^(?:(?:don dat hang|don hang|phieu mua|phieu yeu cau mua)\s+)+",
                            "",
                            normalized(identifier),
                        )
                    )
                )
                rows = exact or [
                    r
                    for r in rows
                    if tokens
                    <= set(
                        words(
                            " ".join(
                                str(r.get(f) or "")
                                for f in (code, "notes", "status", "order_date", "created_at")
                            )
                        )
                    )
                ]
            if truncated or len(rows) != 1:
                items = [
                    {
                        f: r.get(f)
                        for f in (key, code, "status", "order_date", "created_at", "notes")
                        if f in r
                    }
                    for r in rows[:10]
                ]
                for item in items:
                    if isinstance(item.get("notes"), str):
                        item["notes"] = item["notes"][:300]
                return {
                    "status": "partial" if truncated else "ambiguous" if rows else "not_found",
                    "items": items,
                    "match_count": len(rows),
                    "truncated": truncated or len(rows) > 10,
                    "missing": ["identifier"],
                    "questions": [
                        f"Bạn chọn {label} nào trong các mã đã tìm thấy?"
                        if items
                        else f"Bạn cho thêm mã hoặc ngày của {label} để tìm tiếp nhé."
                    ],
                    "warnings": ["Purchase list scan incomplete"] if truncated else [],
                }
            selected = str(UUID(rows[0][key]))
        result = await self.request("GET", path + "/" + selected)
        if result.get("status") != "error" and result.get(key) != selected:
            return failure("INVALID_BACKEND_RESPONSE")
        return result

    async def execute(self, name: str, values: dict) -> dict:
        try:
            schema = DATA_SPECS[name][0]
            args = schema.model_validate(values).model_dump(mode="json")
        except (KeyError, ValidationError, ValueError):
            return failure("INVALID_TOOL_ARGUMENTS")
        if any(isinstance(v, str) and not words(v) for v in args.values()):
            return failure("INVALID_TOOL_ARGUMENTS")
        key = json.dumps([name, args], sort_keys=True)
        async with self.lock:
            if key not in self.cache:
                if name == "catalog_resolve_entities":
                    result = await self.resolve(**args)
                elif name == "catalog_list":
                    result = await self.browse(**args)
                elif name == "catalog_read_offers":
                    result = await self.quotes(**args)
                else:
                    kind = (
                        "purchase-requests" if name == "purchase_get_request" else "purchase-orders"
                    )
                    result = await self.purchase_lookup(kind, **args)
                self.cache[key] = result
            return self.cache[key]
