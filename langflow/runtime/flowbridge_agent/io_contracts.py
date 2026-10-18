# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import json
from copy import deepcopy
from functools import lru_cache

from pydantic import BaseModel

from flowbridge.supplier_assistant.contract import application_documents
from flowbridge.supplier_assistant.prediction.schemas import (
    OrderRisk,
    RankResponse,
    ScenarioResponse,
)
from flowbridge_agent.specs import HANDOFFS, TITLES

PRODUCT = "00000001-0001-4001-8001-000000000001"
SUPPLIER = "00000002-0002-4002-8002-000000000002"
ORDER = "00000003-0003-4003-8003-000000000003"
PREDICTION = "00000004-0004-4004-8004-000000000004"

EXAMPLES = {
    "catalog_find_products": {"query": "Laptop", "limit": 5},
    "catalog_find_suppliers": {"query": "NCC-001", "limit": 5},
    "catalog_get_supplier_offers": {"product_id": PRODUCT, "supplier_id": None},
    "purchase_get_request": {"identifier": "PR-001"},
    "purchase_get_order": {"identifier": "PO-001"},
    "prediction_rank_suppliers": {
        "product_id": PRODUCT,
        "quantity": "100",
        "requested_delivery_date": "2026-12-01",
        "top_k": 3,
        "ranking_policy": "risk_first",
        "currency": "VND",
        "max_unit_price": "18000000",
    },
    "prediction_score_supplier": {
        "product_id": PRODUCT,
        "supplier_id": SUPPLIER,
        "quantity": "100",
        "requested_delivery_date": "2026-12-01",
    },
    "prediction_get_order_risk": {"order_id": ORDER},
    "prediction_get_result": {"prediction_id": PREDICTION},
    "knowledge_search_supplier": {
        "query": "Lịch sử giao hàng và khiếu nại",
        "supplier_id": SUPPLIER,
        "top_k": 3,
    },
}


def input_template(schema: type[BaseModel]) -> dict:
    return {
        key: None if field.is_required() else deepcopy(field.default)
        for key, field in schema.model_fields.items()
    }


def contract_display(action: str, schema: type[BaseModel]) -> dict:
    dump = lambda value: json.dumps(value, ensure_ascii=False, indent=2)  # noqa: E731
    return {
        "title": TITLES[action],
        "version": "1",
        "subtitle": "Mẫu tham khảo, không tự chạy. UUID ví dụ không phải dữ liệu thật.",
        "sections": [
            {"heading": "Input JSON mặc định", "text": dump(input_template(schema))},
            {"heading": "Ví dụ input hợp lệ", "text": dump(EXAMPLES[action])},
            {
                "heading": "Output JSON chung (cấu trúc)",
                "text": dump(
                    {
                        "tool": action,
                        "status": "chua_chay",
                        "input": {},
                        "data": None,
                        "error": None,
                    }
                ),
            },
            {"heading": "Chuyển dữ liệu sang node khác", "items": HANDOFFS[action]},
            {
                "heading": "Quy tắc",
                "text": "Agent nhận công cụ, không nhận JSON mẫu này làm kết quả. "
                "Để input bắt buộc trống cho agent hỏi/tra cứu; null chỉ là chỗ chưa có dữ liệu. "
                "JSON đầu vào trên node là mặc định, câu hỏi có thể ghi đè khi chưa khóa. "
                "Xem output Hợp đồng JSON để có schema API và output đầy đủ.",
            },
        ],
    }


@lru_cache(maxsize=16)
def payload_schema(action: str) -> dict:
    models = {
        "prediction_rank_suppliers": RankResponse,
        "prediction_score_supplier": ScenarioResponse,
        "prediction_get_order_risk": OrderRisk,
    }
    if action in models:
        return models[action].model_json_schema()
    names = {
        "purchase_get_request": "PurchaseRequest",
        "purchase_get_order": "PurchaseOrder",
        "knowledge_search_supplier": "RetrievalResponse",
        "prediction_get_result": "Prediction",
    }
    catalogs = {
        "catalog_find_products": (
            "Product",
            ["product_id", "product_code", "product_name", "unit", "product_category"],
        ),
        "catalog_find_suppliers": (
            "Supplier",
            ["supplier_id", "supplier_code", "supplier_name", "city", "country_of_origin"],
        ),
        "catalog_get_supplier_offers": ("SupplierProduct", None),
    }
    if action not in names and action not in catalogs:
        return {"type": "object", "description": "Bản ghi prediction đã lưu do API trả về."}
    definitions = application_documents()[0]["components"]["schemas"]
    needed = {}

    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: rewrite(item) for key, item in value.items() if not key.startswith("x-")}
        ref = result.get("$ref", "")
        if ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[-1]
            result["$ref"] = "#/$defs/" + name
            if name not in needed:
                needed[name] = {}
                needed[name] = rewrite(definitions[name])
        return result

    if action in catalogs:
        name, columns = catalogs[action]
        item = deepcopy(definitions[name])
        if columns:
            item["properties"] = {
                key: value for key, value in item["properties"].items() if key in columns
            }
            item["required"] = [key for key in item["required"] if key in columns]
        output = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {"enum": ["success", "ambiguous", "truncated", "not_found"]},
                "items": {"type": "array", "items": rewrite(item)},
                "truncated": {"type": "boolean"},
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "items", "truncated", "warnings"],
        }
    else:
        item = deepcopy(definitions[names[action]])
        if action == "prediction_get_result":
            # Same persisted projection as PredictionService.prediction; do not expose features.
            columns = {
                "prediction_id",
                "request_id",
                "request_item_id",
                "po_item_id",
                "supplier_id",
                "product_id",
                "quantity",
                "requested_delivery_date",
                "feature_asof",
                "predicted_at",
                "status",
                "risk_score",
                "risk_threshold",
                "risk_level",
                "feature_schema_version",
                "preprocessing_version",
                "model_version",
                "context_dataset_version",
                "error_code",
            }
            item["properties"] = {
                key: value for key, value in item["properties"].items() if key in columns
            }
            item["properties"].update(
                {
                    "score_is_calibrated": {"const": False},
                    "calibrated_late_probability": {"type": "null"},
                }
            )
            item["required"] = list(item["properties"])
        output = rewrite(item)
    output["$defs"] = needed
    return output
