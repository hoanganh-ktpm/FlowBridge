# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import asyncio
import hashlib
import json
import unicodedata
from copy import deepcopy
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, create_model

from flowbridge.supplier_assistant.knowledge.models import RetrievalRequest
from flowbridge.supplier_assistant.prediction.schemas import (
    OrderRisk,
    RankRequest,
    RankResponse,
    ScenarioRequest,
    ScenarioResponse,
)
from flowbridge_agent.config import BackendConfig
from flowbridge_agent.specs import DESCRIPTIONS, PARAMETER_HELP


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchInput(ToolInput):
    query: Annotated[str, Field(min_length=1, max_length=120, description="Tên/mã/UUID, không SQL")]
    limit: Annotated[int, Field(ge=1, le=10, strict=True, description="Số kết quả tối đa")] = 5


class OffersInput(ToolInput):
    product_id: UUID
    supplier_id: UUID | None = None


class PurchaseInput(ToolInput):
    identifier: Annotated[
        str,
        Field(min_length=1, max_length=120, pattern=r"^[\w\-]+$", description="UUID hoặc mã PR/PO"),
    ]


class RankInput(RankRequest):
    quantity: StrictStr = Field(description="Số lượng > 0, chuỗi decimal, cùng unit sản phẩm")
    max_unit_price: StrictStr | None = Field(
        default=None, description="Giá trần/đơn vị; cần currency"
    )


class ScenarioInput(ScenarioRequest):
    quantity: StrictStr = Field(description="Số lượng > 0, chuỗi decimal, cùng unit sản phẩm")


class OrderInput(ToolInput):
    order_id: UUID


class PredictionInput(ToolInput):
    prediction_id: UUID


class KnowledgeInput(ToolInput):
    query: Annotated[str, Field(min_length=1, max_length=2000)]
    supplier_id: UUID | None = None
    top_k: Annotated[int, Field(ge=1, le=20, strict=True)] = 3


def validate_arguments(action: str, values: dict) -> dict:
    parsed = TOOL_SPECS[action][0].model_validate(values)
    if isinstance(parsed, (SearchInput, KnowledgeInput)) and not normalized(parsed.query):
        raise ValueError("Query must not be blank")
    if action == "prediction_rank_suppliers":
        RankRequest.model_validate(parsed.model_dump())
    elif action == "prediction_score_supplier":
        ScenarioRequest.model_validate(parsed.model_dump())
    elif action == "knowledge_search_supplier":
        RetrievalRequest.model_validate(parsed.model_dump(exclude_none=True))
    return parsed.model_dump(mode="json")


def normalized(value: str) -> str:
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", value.casefold().replace("đ", "d"))
        if not unicodedata.combining(ch)
    ).strip()


def failure(code: str, *, http_status: int | None = None) -> dict:
    return {
        "status": "error",
        "code": code,
        "http_status": http_status,
        "retryable": False,
        "data": None,
    }


class BackendTools:
    # A toolset instance/cache belongs to one graph build, never a global user cache.
    def __init__(self, config: BackendConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.transport = transport
        self.cache: dict[str, dict] = {}
        self.ranked_scores: dict[tuple[str, str, str, str], dict] = {}
        self.lock = asyncio.Lock()

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        params: dict | None = None,
        knowledge: bool = False,
    ) -> dict:
        token = self.config.ai_token if knowledge else self.config.user_token
        if not token:
            return failure("BACKEND_CREDENTIALS_MISSING")
        headers = {"Authorization": "Bearer " + token}
        if knowledge:
            if not self.config.job_context:
                return failure("JOB_CONTEXT_REQUIRED")
            headers["X-Job-Context"] = self.config.job_context
            headers["Idempotency-Key"] = (
                "rag-" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            )
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(self.config.timeout, connect=5),
            ) as http:
                response = await http.request(
                    method,
                    self.config.origin.rstrip("/") + path,
                    headers=headers,
                    json=body,
                    params=params,
                )
            if not response.is_success:
                # Never reflect arbitrary upstream text, request data or credentials.
                return failure("BACKEND_REQUEST_FAILED", http_status=response.status_code)
            value = response.json()
            if not isinstance(value, dict):
                return failure("INVALID_BACKEND_RESPONSE")
            return value
        except httpx.TimeoutException:
            return failure("BACKEND_TIMEOUT")
        except (httpx.HTTPError, ValueError):
            return failure("BACKEND_UNAVAILABLE")

    async def pages(self, path: str) -> tuple[list[dict], bool, dict | None]:
        rows: list[dict] = []
        for page in range(1, self.config.max_pages + 1):
            result = await self.request("GET", path, params={"page": page, "page_size": 100})
            if result.get("status") == "error":
                return [], False, result
            items, total = result.get("items"), result.get("total")
            if (
                not isinstance(items, list)
                or any(not isinstance(item, dict) for item in items)
                or type(total) is not int
                or total < 0
                or len(items) > 100
            ):
                return [], False, failure("INVALID_BACKEND_RESPONSE")
            rows.extend(items)
            if page * 100 >= total:
                return rows, False, None
            if not items:
                return rows, True, None
        return rows, True, None

    async def find(self, resource: str, query: str, limit: int) -> dict:
        rows, truncated, error = await self.pages("/api/v1/" + resource)
        if error:
            return error
        stem = "product" if resource == "products" else "supplier"
        key = normalized(query)
        fields = [stem + "_id", stem + "_code", stem + "_name"]
        exact = [row for row in rows if any(normalized(str(row.get(f, ""))) == key for f in fields)]
        matches = exact or [
            row for row in rows if any(key in normalized(str(row.get(f, ""))) for f in fields[1:])
        ]
        keep = fields + (
            ["unit", "product_category"] if stem == "product" else ["city", "country_of_origin"]
        )
        output = [{f: row.get(f) for f in keep} for row in matches[:limit]]
        complete = not truncated and len(matches) <= limit
        return {
            "status": "truncated"
            if not complete
            else ("not_found" if not matches else "success" if len(matches) == 1 else "ambiguous"),
            "items": output,
            "truncated": not complete,
            "warnings": ["Catalog scan incomplete; do not assume a unique match"]
            if not complete
            else [],
        }

    async def offers(self, product_id: str, supplier_id: str | None) -> dict:
        rows, truncated, error = await self.pages("/api/v1/supplier-products")
        if error:
            return error
        rows = [
            r
            for r in rows
            if str(r.get("product_id")) == product_id
            and (supplier_id is None or str(r.get("supplier_id")) == supplier_id)
        ]
        return {
            "status": "truncated"
            if truncated or len(rows) > 20
            else "success"
            if rows
            else "not_found",
            "items": rows[:20],
            "truncated": truncated or len(rows) > 20,
            "warnings": ["Quotes are not supplier confirmation of available quantity"],
        }

    async def purchase(self, kind: str, identifier: str) -> dict:
        path = "/api/v1/" + kind
        try:
            identifier = str(UUID(identifier))
        except ValueError:
            rows, truncated, error = await self.pages(path)
            if error:
                return error
            code = "request_code" if kind == "purchase-requests" else "po_number"
            key = "request_id" if kind == "purchase-requests" else "purchase_order_id"
            rows = [r for r in rows if normalized(str(r.get(code, ""))) == normalized(identifier)]
            if truncated or len(rows) != 1:
                return {
                    "status": "truncated" if truncated else "ambiguous" if rows else "not_found",
                    "items": [{code: r.get(code), key: r.get(key)} for r in rows[:10]],
                }
            identifier = str(UUID(str(rows[0][key])))
        return await self.request("GET", path + "/" + identifier)

    async def execute(self, name: str, values: dict) -> dict:
        try:
            args = validate_arguments(name, values)
        except (KeyError, ValidationError, ValueError):
            return failure("INVALID_TOOL_ARGUMENTS")
        cache_key = json.dumps([name, args], sort_keys=True)
        async with self.lock:
            if cache_key in self.cache:
                return self.cache[cache_key]
            result = await self._execute(name, args)
            if len(json.dumps(result, ensure_ascii=False)) > 12000:
                result = failure("RESULT_TOO_LARGE_NARROW_REQUEST")
            self.cache[cache_key] = result
            return result

    async def _execute(self, name: str, args: dict) -> dict:
        if name == "catalog_find_products":
            return await self.find("products", **args)
        if name == "catalog_find_suppliers":
            return await self.find("suppliers", **args)
        if name == "catalog_get_supplier_offers":
            return await self.offers(**args)
        if name in {"purchase_get_request", "purchase_get_order"}:
            kind = "purchase-requests" if name.endswith("request") else "purchase-orders"
            return await self.purchase(kind, **args)
        if name == "knowledge_search_supplier":
            return await self.request(
                "POST",
                "/internal/v1/tools/retrieve-knowledge",
                body={key: value for key, value in args.items() if value is not None},
                knowledge=True,
            )
        if name == "prediction_rank_suppliers":
            method, path, schema = "POST", "/api/v1/ml/suppliers/rank", RankResponse
        elif name == "prediction_score_supplier":
            key = self.scenario_key(args)
            if key in self.ranked_scores:
                return self.ranked_scores[key]
            method, path, schema = "POST", "/api/v1/ml/scenarios/predict", ScenarioResponse
        elif name == "prediction_get_order_risk":
            method, path, schema = (
                "GET",
                f"/api/v1/ml/purchase-orders/{args['order_id']}/risk",
                OrderRisk,
            )
        else:
            method, path, schema = "GET", f"/api/v1/ml/predictions/{args['prediction_id']}", None
        result = await self.request(method, path, body=args if method == "POST" else None)
        if result.get("status") == "error" or schema is None:
            return result
        try:
            result = schema.model_validate(result).model_dump(mode="json")
        except ValidationError:
            return failure("INVALID_BACKEND_RESPONSE")
        if name == "prediction_rank_suppliers":
            for candidate in result["candidates"] + result.get("unscored", []):
                scenario = {
                    key: args[key] for key in ("product_id", "quantity", "requested_delivery_date")
                }
                scenario["supplier_id"] = candidate["supplier_id"]
                self.ranked_scores[self.scenario_key(scenario)] = {
                    "scenario": scenario,
                    "result": candidate["result"],
                    "trace": result["trace"],
                }
        return result

    @staticmethod
    def scenario_key(args: dict) -> tuple[str, str, str, str]:
        return (
            args["product_id"],
            args["supplier_id"],
            str(Decimal(args["quantity"]).normalize()),
            args["requested_delivery_date"],
        )


_ACTION_MODELS = {
    "catalog_find_products": (SearchInput, "catalog"),
    "catalog_find_suppliers": (SearchInput, "catalog"),
    "catalog_get_supplier_offers": (OffersInput, "catalog"),
    "purchase_get_request": (PurchaseInput, "purchase"),
    "purchase_get_order": (PurchaseInput, "purchase"),
    "prediction_rank_suppliers": (RankInput, "prediction"),
    "prediction_score_supplier": (ScenarioInput, "prediction"),
    "prediction_get_order_risk": (OrderInput, "prediction"),
    "prediction_get_result": (PredictionInput, "prediction"),
    "knowledge_search_supplier": (KnowledgeInput, "knowledge"),
}
TOOL_SPECS: dict[str, tuple[type[BaseModel], str, str]] = {
    name: (schema, group, DESCRIPTIONS[name]) for name, (schema, group) in _ACTION_MODELS.items()
}


def build_tools(group: str, client: BackendTools) -> list[BaseTool]:
    tools = []
    for name, (_schema, category, description) in TOOL_SPECS.items():
        if category != group:
            continue
        if category == "knowledge" and not (client.config.ai_token and client.config.job_context):
            continue

        tools.append(configured_tool(name, client, name=name, description=description, values={}))
    return tools


class ActionTool(BaseTool):
    # Only args_schema is sent to the model. Runtime settings stay outside tool arguments.
    action: str
    client: BackendTools = Field(exclude=True, repr=False)
    configured_inputs: dict = Field(default_factory=dict, exclude=True)
    bound_inputs: dict = Field(default_factory=dict, exclude=True)
    clearable_fields: tuple[str, ...] = ()
    output_fields: tuple[str, ...] = ()

    def _run(self, **kwargs: Any) -> dict:
        raise NotImplementedError("Use the asynchronous Langflow agent or JSON Result output")

    async def _arun(self, **kwargs: Any) -> dict:
        try:
            values = self.resolve_arguments(kwargs)
        except ValidationError as exc:
            return tool_result(self.action, {}, argument_failure(exc))
        except ValueError:
            return tool_result(self.action, {}, failure("INVALID_TOOL_ARGUMENTS"))
        result = await self.client.execute(self.action, values)
        return tool_result(self.action, values, project_output(result, self.output_fields))

    def resolve_arguments(self, supplied: dict) -> dict:
        supplied = dict(supplied)
        clear = supplied.pop("clear_defaults", [])
        if not isinstance(clear, list) or not set(clear) <= set(self.clearable_fields):
            raise ValueError("Only explicitly clear configured optional filters")
        if set(supplied) & self.bound_inputs.keys():
            raise ValueError("Locked inputs are configured by the operator")
        unknown = set(supplied) - TOOL_SPECS[self.action][0].model_fields.keys()
        if unknown:
            raise ValueError("Unknown tool argument")
        # Null means no override. Only the explicit clear_defaults control removes a soft filter.
        values = {**self.configured_inputs, **{k: v for k, v in supplied.items() if v is not None}}
        values.update({key: None for key in clear})
        values.update(self.bound_inputs)
        return validate_arguments(self.action, values)


class ToolFailure(ToolInput):
    code: str
    http_status: int | None
    retryable: bool = False
    fields: list[str] = Field(default_factory=list)


class ToolResult(ToolInput):
    tool: str
    status: str
    input: dict
    data: dict | None
    error: ToolFailure | None


def tool_result(action: str, values: dict, result: dict) -> dict:
    failed = result.get("status") == "error"
    # A PR/PO's business status stays inside data.status, separate from tool outcome.
    status = "error" if failed else "success"
    if action not in {"purchase_get_request", "purchase_get_order"} or "items" in result:
        candidate = result.get("status")
        if candidate in {
            "success",
            "partial",
            "insufficient_data",
            "ambiguous",
            "not_found",
            "truncated",
            "no_eligible_suppliers",
            "no_open_items",
        }:
            status = candidate
    if action == "prediction_score_supplier" and not failed:
        status = result.get("result", {}).get("status", "success")
    return ToolResult(
        tool=action,
        status=status,
        input=values,
        data=None if failed else result,
        error=ToolFailure(
            code=result.get("code", "TOOL_FAILED"),
            http_status=result.get("http_status"),
            retryable=False,
            fields=result.get("fields", []),
        )
        if failed
        else None,
    ).model_dump(mode="json")


def argument_failure(error: ValidationError) -> dict:
    fields = {
        str(detail["loc"][0])
        for detail in error.errors(include_input=False)
        if detail["loc"] and str(detail["loc"][0]) in PARAMETER_HELP
    }
    return {**failure("INVALID_TOOL_ARGUMENTS"), "fields": sorted(fields)}


def invalid_tool_result(error: ValidationError) -> str:
    return json.dumps(tool_result("validation", {}, argument_failure(error)))


def project_output(result: dict, fields: tuple[str, ...]) -> dict:
    if not fields or result.get("status") == "error":
        return result
    # Preserve interpretation/provenance even when the operator selects a compact output.
    keep = set(fields) | {
        "status",
        "warnings",
        "truncated",
        "trace",
        "is_complete",
        "aggregation_policy",
        "score_is_calibrated",
        "unscored",
        "excluded",
        "considered_count",
    }
    unknown = set(fields) - result.keys()
    if unknown:
        return failure("INVALID_OUTPUT_FIELDS")
    return {key: value for key, value in result.items() if key in keep}


def configured_tool(
    action: str,
    client: BackendTools,
    *,
    name: str,
    description: str,
    values: dict,
    lock_inputs: bool = False,
    output_fields: tuple[str, ...] = (),
) -> ActionTool:
    schema = TOOL_SPECS[action][0]
    unknown = set(values) - schema.model_fields.keys()
    if unknown:
        raise ValueError("Input JSON contains fields outside this action's API contract")
    validated = {
        key: deepcopy(field.default)
        for key, field in schema.model_fields.items()
        if not field.is_required()
    }
    for key, value in values.items():
        field = deepcopy(schema.model_fields[key])
        validator = create_model(
            "ConfiguredField", __base__=ToolInput, **{key: (field.annotation, field)}
        )
        validated[key] = validator.model_validate({key: value}).model_dump(mode="json")[key]
    fields = {}
    bound = (
        {key: value for key, value in validated.items() if value is not None} if lock_inputs else {}
    )
    clearable = tuple(
        key
        for key, value in validated.items()
        if value is not None
        and key not in bound
        and not schema.model_fields[key].is_required()
        and schema.model_fields[key].default is None
    )
    for key, original in schema.model_fields.items():
        if key in bound:
            continue
        field = deepcopy(original)
        field.description = PARAMETER_HELP.get(key, field.description or key)
        annotation = field.annotation
        if key in validated:
            annotation = annotation | None
            field.default = None
            field.description += "; null dùng mặc định node: " + json.dumps(
                validated[key], ensure_ascii=False
            )
        fields[key] = (annotation, field)
    if clearable:
        fields["clear_defaults"] = (
            list[Literal[*clearable]],
            Field(
                default_factory=list,
                description="Chỉ khi người dùng nói rõ bỏ bộ lọc mặc định; "
                "liệt kê field cần bỏ. Bình thường gửi []. Không áp dụng input khóa.",
            ),
        )
    # Canonical validation still runs on the complete merged arguments before any HTTP request.
    agent_schema = create_model(name + "Input", __base__=ToolInput, **fields)
    if bound:
        description += " Input cố định tại node: " + json.dumps(bound, ensure_ascii=False)
    description += " Output JSON: {tool,status,input,data,error}. Đọc status trước data; "
    description += (
        "input là tham số thực tế sau hợp nhất. Giá trị null của input tool dùng mặc định."
    )
    return ActionTool(
        name=name,
        description=description,
        args_schema=agent_schema,
        action=action,
        client=client,
        configured_inputs=validated,
        bound_inputs=bound,
        clearable_fields=clearable,
        output_fields=output_fields,
        handle_validation_error=invalid_tool_result,
    )
