# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Intent(StrEnum):
    RESOLVE = "resolve_entities"
    PRODUCTS = "list_products"
    SUPPLIERS = "list_suppliers"
    OFFERS = "offers"
    REQUEST = "request"
    ORDER = "order"
    RANK = "rank_suppliers"
    SCORE = "score_supplier"
    RISK = "order_risk"
    RESULT = "stored_result"
    RETRIEVE = "retrieve"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Slots(Strict):
    # Nullable fields are required in the provider schema; null means no new information.
    product: str | None
    supplier: str | None
    request: str | None
    order: str | None
    prediction: str | None
    quantity: str | None
    unit: str | None
    delivery_date: str | None
    relative_days: int | None
    currency: str | None
    max_unit_price: str | None
    top_k: int | None
    query: str | None
    page: int | None


class Decision(Strict):
    mode: Literal["chat", "new", "update", "clarify", "complex"]
    tasks: list[Intent]
    slots: Slots
    selection: str | None
    clear: list[Literal["supplier", "currency", "max_unit_price", "quantity", "delivery_date"]]
    reply: str | None


class ServiceRequest(Strict):
    version: Literal[3] = 3
    run_id: str
    task_id: str
    service: Literal["database", "prediction", "knowledge"]
    task: Intent
    arguments: dict


ROUTES = {
    Intent.RESOLVE: ("database", "catalog_resolve_entities"),
    Intent.PRODUCTS: ("database", "catalog_list"),
    Intent.SUPPLIERS: ("database", "catalog_list"),
    Intent.OFFERS: ("database", "catalog_read_offers"),
    Intent.REQUEST: ("database", "purchase_get_request"),
    Intent.ORDER: ("database", "purchase_get_order"),
    Intent.RANK: ("prediction", "prediction_rank_suppliers"),
    Intent.SCORE: ("prediction", "prediction_score_supplier"),
    Intent.RISK: ("prediction", "prediction_get_order_risk"),
    Intent.RESULT: ("prediction", "prediction_get_result"),
    Intent.RETRIEVE: ("knowledge", "knowledge_search_supplier"),
}


def normalize_slots(slots: Slots, business_date: str) -> dict:
    values = slots.model_dump(exclude_none=True)
    if any(isinstance(v, str) and (not v or len(v) > 2000) for v in values.values()):
        raise ValueError("INVALID_TEXT_INPUT")
    for name in ("quantity", "max_unit_price"):
        if name not in values:
            continue
        try:
            value = Decimal(values[name].replace(",", "."))
        except InvalidOperation:
            raise ValueError("INVALID_DECIMAL") from None
        if not value.is_finite() or value < 0 or (name == "quantity" and value == 0):
            raise ValueError("INVALID_DECIMAL")
        if len(value.as_tuple().digits) > 18 or value.as_tuple().exponent < -4:
            raise ValueError("INVALID_DECIMAL")
        values[name] = format(value, "f")
    anchor = date.fromisoformat(business_date)
    relative = values.pop("relative_days", None)
    if relative is not None:
        if not 0 <= relative <= 3660 or "delivery_date" in values:
            raise ValueError("CONFLICTING_DATE")
        values["delivery_date"] = (anchor + timedelta(days=relative)).isoformat()
    if "delivery_date" in values:
        parsed = date.fromisoformat(values["delivery_date"])
        if parsed < anchor:
            raise ValueError("DELIVERY_DATE_PASSED")
        values["delivery_date"] = parsed.isoformat()
    for name, maximum in (("top_k", 20), ("page", 10000)):
        if name in values and not 1 <= values[name] <= maximum:
            raise ValueError("INVALID_RANGE")
    if "currency" in values:
        currency = values["currency"].upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("INVALID_CURRENCY")
        values["currency"] = currency
    return values
