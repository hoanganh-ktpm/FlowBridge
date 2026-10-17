# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import json
import re
from copy import deepcopy
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI
from lfx.components.models_and_agents.agent import AgentComponent
from lfx.custom import Component
from lfx.field_typing import LanguageModel, Tool
from lfx.io import (
    BoolInput,
    DataDisplayInput,
    DropdownInput,
    FloatInput,
    HandleInput,
    IntInput,
    MultilineInput,
    Output,
    SecretStrInput,
    StrInput,
)
from lfx.schema.data import Data

from flowbridge_agent.config import MODEL, BackendConfig, local_values
from flowbridge_agent.io_contracts import contract_display, payload_schema
from flowbridge_agent.prompt import SYSTEM_PROMPT
from flowbridge_agent.specs import HANDOFFS, PARAMETER_HELP, TITLES
from flowbridge_agent.tools import (
    TOOL_SPECS,
    ActionTool,
    BackendTools,
    ToolResult,
    configured_tool,
    failure,
    tool_result,
)


def secret_value(value: Any) -> str:
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value or "")


class StrictLunaChat(ChatOpenAI):
    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        kwargs.update(strict=True, parallel_tool_calls=False)
        return super().bind_tools(tools, **kwargs)


class LunaModelBase(Component):
    display_name = "Mô hình OpenAI"
    description = "Cung cấp model cho trợ lý; có thể đổi model, khóa API và giới hạn đầu ra."
    icon = "OpenAI"
    inputs = [
        DropdownInput(
            name="model_name",
            display_name="Mô hình",
            value=MODEL,
            options=[MODEL, "gpt-4.1-mini", "gpt-4o-mini"],
            combobox=True,
            info="Chọn hoặc gõ model ID OpenAI hỗ trợ tool calling mà tài khoản có quyền dùng.",
        ),
        SecretStrInput(
            name="api_key",
            display_name="Khóa API OpenAI",
            value="",
            info="Trống: dùng OPENAI_API_KEY trong data/agent.env hoặc biến môi trường.",
        ),
        IntInput(name="output_tokens", display_name="Giới hạn token đầu ra", value=600),
        DropdownInput(
            name="reasoning_effort",
            display_name="Mức suy luận",
            value="low",
            options=["auto", "none", "low", "medium", "high", "xhigh", "max"],
            info="auto: không gửi tham số. Bỏ qua với model không thuộc họ reasoning.",
        ),
        BoolInput(
            name="send_temperature",
            display_name="Gửi tham số temperature",
            value=False,
            info="Chỉ bật khi model hỗ trợ temperature với reasoning đang dùng.",
            advanced=True,
        ),
        FloatInput(
            name="temperature", display_name="Độ ngẫu nhiên (temperature)", value=0.1, advanced=True
        ),
        IntInput(name="timeout", display_name="Thời gian chờ (giây)", value=45, advanced=True),
        IntInput(
            name="max_retries",
            display_name="Số lần thử lại SDK",
            value=0,
            advanced=True,
            info="0 tránh tự gọi lại phát sinh phí. Có thể chỉnh 0–3.",
        ),
    ]
    outputs = [Output(name="model", display_name="Mô hình ngôn ngữ", method="build_model")]

    def build_model(self) -> LanguageModel:
        key = secret_value(self.api_key) or local_values().get("OPENAI_API_KEY", "")
        if not key:
            raise ValueError("Nhập OpenAI API key tại node hoặc trong data/agent.env")
        name = str(self.model_name).strip()
        if not name or not 1 <= int(self.output_tokens) <= 128000:
            raise ValueError("Chọn model và max output tokens trong khoảng 1–128000")
        if not 1 <= int(self.timeout) <= 600 or not 0 <= int(self.max_retries) <= 3:
            raise ValueError("Timeout: 1–600 giây; SDK retries: 0–3")
        if not 0 <= float(self.temperature) <= 2:
            raise ValueError("Temperature phải từ 0 đến 2")
        effort = self.reasoning_effort
        reasoning_model = name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))
        return StrictLunaChat(
            model=name,
            api_key=key,
            temperature=float(self.temperature) if self.send_temperature else None,
            max_tokens=int(self.output_tokens),
            reasoning_effort=effort if reasoning_model and effort != "auto" else None,
            max_retries=int(self.max_retries),
            timeout=int(self.timeout),
            use_responses_api=False,
            streaming=False,
            model_kwargs={"store": False},
        )


# Method/path follow the contract; the LLM cannot supply arbitrary URLs or SQL.
ACTION_ROUTES = {
    "catalog_find_products": "GET /api/v1/products (bounded name/code scan)",
    "catalog_find_suppliers": "GET /api/v1/suppliers (bounded name/code scan)",
    "catalog_get_supplier_offers": "GET /api/v1/supplier-products (product/supplier filter)",
    "purchase_get_request": "GET /api/v1/purchase-requests/{request_id}",
    "purchase_get_order": "GET /api/v1/purchase-orders/{order_id}",
    "prediction_rank_suppliers": "POST /api/v1/ml/suppliers/rank",
    "prediction_score_supplier": "POST /api/v1/ml/scenarios/predict",
    "prediction_get_order_risk": "GET /api/v1/ml/purchase-orders/{order_id}/risk",
    "prediction_get_result": "GET /api/v1/ml/predictions/{prediction_id}",
    "knowledge_search_supplier": "POST /internal/v1/tools/retrieve-knowledge",
}


def argument_inputs(action: str) -> list:
    labels = {
        "query": "Nội dung tìm kiếm",
        "limit": "Số kết quả tra cứu",
        "product_id": "UUID sản phẩm",
        "supplier_id": "UUID nhà cung cấp",
        "identifier": "Mã hoặc UUID yêu cầu/đơn hàng",
        "quantity": "Số lượng (chuỗi decimal)",
        "requested_delivery_date": "Ngày cần giao (YYYY-MM-DD)",
        "top_k": "Số kết quả (top-k)",
        "currency": "Tiền tệ (VND, USD…)",
        "max_unit_price": "Trần đơn giá (chuỗi decimal)",
        "ranking_policy": "Chính sách xếp hạng",
        "order_id": "UUID đơn đặt hàng (PO)",
        "prediction_id": "UUID dự đoán đã lưu",
    }
    fields = []
    for key, field in TOOL_SPECS[action][0].model_fields.items():
        value = "" if field.is_required() or field.default is None else field.default
        common = {"name": key, "display_name": labels.get(key, key), "value": value}
        if key in {"limit", "top_k"}:
            fields.append(IntInput(**common, info="Mặc định cho tool; bật Lock inputs để cố định."))
        elif key == "ranking_policy":
            fields.append(
                DropdownInput(
                    **common, options=["risk_first"], info="Contract hiện chỉ hỗ trợ risk_first."
                )
            )
        else:
            fields.append(
                StrInput(
                    **common,
                    info=(
                        PARAMETER_HELP.get(key, "")
                        + " Trống: agent điền. Có giá trị: mặc định cho "
                        "agent và chạy trực tiếp; bật khóa nếu không cho thay đổi."
                    ),
                )
            )
    return fields


def action_inputs(action: str) -> list:
    knowledge = TOOL_SPECS[action][1] == "knowledge"
    items = [
        BoolInput(name="enabled", display_name="Bật công cụ", value=True),
        StrInput(
            name="tool_name",
            display_name="Tên hàm công cụ",
            value=action,
            advanced=True,
            info="Tên agent nhìn thấy; nếu đổi hãy cập nhật hướng dẫn agent tương ứng.",
        ),
        MultilineInput(
            name="tool_description", display_name="Mô tả sử dụng", value=TOOL_SPECS[action][2]
        ),
        DataDisplayInput(
            name="io_contract",
            display_name="Input/output và luồng dữ liệu",
            button_text="Xem JSON và cách nối",
            value=contract_display(action, TOOL_SPECS[action][0]),
        ),
        *argument_inputs(action),
        MultilineInput(
            name="input_json",
            display_name="Đầu vào JSON mặc định",
            value="{}",
            info="JSON object bổ sung/ghi đè các ô tham số trên. Không chứa token, URL hay SQL.",
        ),
        BoolInput(
            name="lock_inputs",
            display_name="Khóa giá trị đã cấu hình",
            value=False,
            info="Bật: các giá trị có dữ liệu được khóa. Tắt: câu hỏi có thể ghi đè mọi mặc định. "
            "Tham số null từ agent dùng mặc định; trường bắt buộc chưa có thì hỏi lại.",
        ),
        StrInput(
            name="output_fields",
            display_name="Các trường đầu ra (CSV)",
            value="",
            advanced=True,
            info="Trống: toàn bộ data của JSON chung. Ví dụ items hoặc candidates,trace. "
            "Status/cảnh báo/provenance luôn giữ; không đổi tên field.",
        ),
        StrInput(
            name="api_origin",
            display_name="Địa chỉ API",
            value="",
            advanced=True,
            placeholder="http://127.0.0.1:8000",
            info="Trống: FLOWBRIDGE_API_ORIGIN, mặc định localhost:8000. Không thêm path.",
        ),
        SecretStrInput(
            name="access_token",
            display_name="Token dịch vụ AI" if knowledge else "Access token người dùng",
            value="",
            advanced=True,
            info="Trống: token tương ứng trong data/agent.env. Khác với OpenAI API key.",
        ),
        IntInput(name="timeout", display_name="Thời gian chờ API (giây)", value=150, advanced=True),
    ]
    if TOOL_SPECS[action][1] in {"catalog", "purchase"}:
        items.append(
            IntInput(
                name="max_pages",
                display_name="Số trang quét tối đa",
                value=5,
                advanced=True,
                info="1–10; 100 dòng/trang. Thiếu trang báo truncated.",
            )
        )
    if knowledge:
        items.append(
            StrInput(
                name="job_context",
                display_name="UUID JobContext tin cậy",
                value="",
                info="Analysis task thật do hệ thống cấp; trống dùng cấu hình local.",
                advanced=True,
            )
        )
    return items


class ActionNodeBase(Component):
    action = ""
    icon = "Wrench"
    outputs = [
        Output(name="tools", display_name="Công cụ cho agent", method="get_tools"),
        Output(name="json_result", display_name="Kết quả JSON", method="run_json"),
        Output(name="tool_schema", display_name="Hợp đồng JSON", method="schema_json"),
    ]

    def backend(self) -> BackendTools:
        local = BackendConfig.from_local()
        knowledge = TOOL_SPECS[self.action][1] == "knowledge"
        token = secret_value(self.access_token)
        return BackendTools(
            BackendConfig(
                origin=str(self.api_origin or local.origin).strip(),
                user_token=local.user_token if knowledge else token or local.user_token,
                ai_token=(token or local.ai_token) if knowledge else local.ai_token,
                job_context=str(getattr(self, "job_context", "") or local.job_context).strip(),
                max_pages=int(getattr(self, "max_pages", local.max_pages)),
                timeout=int(self.timeout),
            )
        )

    def configured_values(self) -> dict:
        values = {}
        for key in TOOL_SPECS[self.action][0].model_fields:
            value = getattr(self, key, None)
            if value is not None and value != "":
                values[key] = value.strip() if isinstance(value, str) else value
        try:
            payload = json.loads(self.input_json or "{}")
        except (ValueError, TypeError):
            raise ValueError("Input JSON phải là một JSON object hợp lệ") from None
        if not isinstance(payload, dict):
            raise ValueError('Input JSON phải là object, ví dụ {"top_k": 5}')
        return {**values, **payload}

    def make_tool(self) -> ActionTool:
        name = str(self.tool_name).strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
            raise ValueError("Tool name: 1–64 ký tự chữ, số, _ hoặc -")
        description = str(self.tool_description).strip()
        if not description:
            raise ValueError("Description không được để trống")
        return configured_tool(
            self.action,
            self.backend(),
            name=name,
            description=description,
            values=self.configured_values(),
            lock_inputs=bool(self.lock_inputs),
            output_fields=tuple(f.strip() for f in str(self.output_fields).split(",") if f.strip()),
        )

    def get_tools(self) -> list[Tool]:
        if not self.enabled:
            self.status = "Disabled"
            return []
        tool = self.make_tool()
        if TOOL_SPECS[self.action][1] == "knowledge" and not (
            tool.client.config.ai_token and tool.client.config.job_context
        ):
            self.status = "Chưa đăng ký RAG: cần AI service token và JobContext thật"
            return []
        self.status = {"tool": tool.name, "endpoint": ACTION_ROUTES[self.action]}
        return [tool]

    async def run_json(self) -> Data:
        if not self.enabled:
            return Data(data=tool_result(self.action, {}, failure("TOOL_DISABLED")))
        tool = self.make_tool()
        # Only Agent Tool is connected in the agent graph; this is a manual/direct output.
        result = await tool._arun()
        self.status = result
        return Data(data=result)

    def schema_json(self) -> Data:
        tool = self.make_tool()
        group = TOOL_SPECS[self.action][1]
        contract = (
            "backend/src/flowbridge/contracts/app-api.yaml"
            if group in {"catalog", "purchase"}
            else "backend/src/flowbridge/contracts/ai-tools-api.yaml"
            if group == "knowledge"
            else "backend/src/flowbridge/supplier_assistant/prediction/schemas.py"
        )
        return Data(
            data={
                "endpoint": ACTION_ROUTES[self.action],
                "tool": convert_to_openai_tool(tool, strict=True),
                "configured_inputs": tool.configured_inputs,
                "locked_inputs": tool.bound_inputs,
                "output_fields": list(tool.output_fields),
                "response_contract": contract,
                "output_schema": ToolResult.model_json_schema(),
                "success_data_schema": payload_schema(self.action),
                "handoffs": HANDOFFS[self.action],
                "clearable_defaults": list(tool.clearable_fields),
            }
        )


class FindProductsBase(ActionNodeBase):
    action = "catalog_find_products"
    display_name = TITLES[action]
    description = TOOL_SPECS[action][2].split(". ")[0] + "."
    inputs = action_inputs(action)


class FindSuppliersBase(ActionNodeBase):
    action = "catalog_find_suppliers"
    display_name = TITLES[action]
    description = TOOL_SPECS[action][2].split(". ")[0] + "."
    inputs = action_inputs(action)


class SupplierOffersBase(ActionNodeBase):
    action = "catalog_get_supplier_offers"
    display_name = TITLES[action]
    description = TOOL_SPECS[action][2].split(". ")[0] + "."
    inputs = action_inputs(action)


class PurchaseRequestBase(ActionNodeBase):
    action = "purchase_get_request"
    display_name = TITLES[action]
    description = TOOL_SPECS[action][2].split(". ")[0] + "."
    inputs = action_inputs(action)


class PurchaseOrderBase(ActionNodeBase):
    action = "purchase_get_order"
    display_name = TITLES[action]
    description = TOOL_SPECS[action][2].split(". ")[0] + "."
    inputs = action_inputs(action)


class RankSuppliersBase(ActionNodeBase):
    action = "prediction_rank_suppliers"
    display_name = TITLES[action]
    description = TOOL_SPECS[action][2].split(". ")[0] + "."
    inputs = action_inputs(action)


class ScoreSupplierBase(ActionNodeBase):
    action = "prediction_score_supplier"
    display_name = TITLES[action]
    description = TOOL_SPECS[action][2].split(". ")[0] + "."
    inputs = action_inputs(action)


class OrderRiskBase(ActionNodeBase):
    action = "prediction_get_order_risk"
    display_name = TITLES[action]
    description = TOOL_SPECS[action][2].split(". ")[0] + "."
    inputs = action_inputs(action)


class PredictionResultBase(ActionNodeBase):
    action = "prediction_get_result"
    display_name = TITLES[action]
    description = TOOL_SPECS[action][2].split(". ")[0] + "."
    inputs = action_inputs(action)


class RetrieveKnowledgeBase(ActionNodeBase):
    action = "knowledge_search_supplier"
    display_name = TITLES[action]
    description = TOOL_SPECS[action][2].split(". ")[0] + "."
    inputs = action_inputs(action)


def service_inputs(label: str, description: str) -> list:
    return [
        BoolInput(name="enabled", display_name="Bật công cụ", value=True),
        StrInput(name="service_label", display_name="Tên dịch vụ", value=label),
        MultilineInput(name="service_description", display_name="Mô tả sử dụng", value=description),
        HandleInput(
            name="child_tools", display_name="Các công cụ con", input_types=["Tool"], is_list=True
        ),
    ]


class ServiceToolsBase(Component):
    groups: tuple[str, ...] = ()
    outputs = [Output(name="tools", display_name="Công cụ của dịch vụ", method="get_tools")]

    def get_tools(self) -> list[Tool]:
        if not self.enabled:
            return []
        tools, clients, names = [], {}, set()

        def collect(value: Any) -> None:
            if isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)
                return
            if not isinstance(value, ActionTool) or TOOL_SPECS[value.action][1] not in self.groups:
                raise ValueError("Nối đúng node tác vụ thuộc service này vào Child tools")
            if value.name in names:
                raise ValueError("Mỗi tool phải có tên riêng trong cùng service")
            names.add(value.name)
            # Same-service children share a per-build cache, including rank -> scenario reuse.
            client = clients.setdefault(value.client.config, value.client)
            tools.append(
                value.model_copy(
                    update={
                        "client": client,
                        "description": (
                            f"[{self.service_label}] {self.service_description}\n"
                            f"{value.description}"
                        ),
                    }
                )
            )

        collect(self.child_tools or [])
        self.status = {"service": self.service_label, "tools": [tool.name for tool in tools]}
        return tools


class CatalogToolsBase(ServiceToolsBase):
    display_name = "Dịch vụ dữ liệu"
    description = "Gom các tool dữ liệu; mỗi tác vụ vẫn có schema và description riêng."
    icon = "Database"
    groups = ("catalog", "purchase")
    inputs = service_inputs(
        "Dữ liệu", "Xác định đối tượng và đọc báo giá/PR/PO; trả UUID để truyền đúng công cụ."
    )


class PredictionToolsBase(ServiceToolsBase):
    display_name = "Dịch vụ dự đoán"
    description = "Gom top-k, scenario và đọc kết quả ML; không thêm lượt gọi model phụ."
    icon = "ChartNoAxesCombined"
    groups = ("prediction",)
    inputs = service_inputs(
        "Dự đoán",
        "Xếp hạng nhiều NCC, chấm một NCC hoặc đọc rủi ro; chỉ nhận UUID và điều kiện đúng loại.",
    )


class KnowledgeToolsBase(ServiceToolsBase):
    display_name = "Dịch vụ tri thức (RAG)"
    description = "Gom tool truy hồi bằng chứng; cần AI credential và JobContext thật."
    icon = "BookOpen"
    groups = ("knowledge",)
    inputs = service_inputs(
        "Tri thức",
        "Truy hồi bằng chứng theo query và NCC; trả text kèm nguồn, không thay giá/điểm ML.",
    )


class ProcurementAgentBase(AgentComponent):
    display_name = "Trợ lý mua hàng"
    description = "Agent điều phối. Chỉnh prompt, giới hạn lượt model/tool và lịch sử tại node."
    inputs = [
        HandleInput(
            name="model",
            display_name="Mô hình ngôn ngữ",
            input_types=["LanguageModel"],
            required=True,
            info="Nối Mô hình OpenAI vào đây; chỉnh model/API key ở node đó.",
        )
        if item.name == "model"
        else deepcopy(item)
        for item in AgentComponent.inputs
    ] + [
        IntInput(
            name="max_tool_calls",
            display_name="Số lần gọi công cụ mỗi lượt",
            value=3,
            info="Giới hạn cứng 1–30; mặc định 3 để giảm gọi dư.",
        ),
    ]
    outputs = [Output(name="response", display_name="Câu trả lời", method="message_response")]
    for component_input in inputs:
        if component_input.name == "max_iterations":
            component_input.value = 4
            component_input.display_name = "Số lần gọi model mỗi lượt"
            component_input.advanced = False
        elif component_input.name == "system_prompt":
            component_input.display_name = "Hướng dẫn điều phối"
            component_input.value = SYSTEM_PROMPT
        elif component_input.name == "input_value":
            component_input.display_name = "Câu hỏi người dùng"
        elif component_input.name == "tools":
            component_input.display_name = "Các công cụ"
        elif component_input.name == "n_messages":
            component_input.display_name = "Số tin nhắn lịch sử"
            component_input.value = 6
            component_input.advanced = False
        elif component_input.name in {
            "handle_parsing_errors",
            "add_current_date_tool",
            "add_calculator_tool",
            "stream",
        }:
            component_input.value = False

    def _inject_dynamic_prompt_values(self, prompt: Any | None) -> str | None:
        if prompt is not None:
            text = prompt if isinstance(prompt, str) else getattr(prompt, "text", str(prompt))
            business_now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(timespec="seconds")
            prompt = (
                text.replace("{current_date}", business_now)
                .replace("{max_tool_calls}", str(self.max_tool_calls))
                .replace("{max_model_calls}", str(self.max_iterations))
            )
        return super()._inject_dynamic_prompt_values(prompt)

    def _build_middleware(self, llm: Any, *, allow_interrupts: bool = True) -> list:
        if not 1 <= int(self.max_iterations) <= 32 or not 1 <= int(self.max_tool_calls) <= 30:
            raise ValueError("Model calls: 1–32; tool calls: 1–30")
        middleware = [
            item
            for item in super()._build_middleware(llm, allow_interrupts=allow_interrupts)
            if type(item).__name__ != "ToolRetryMiddleware"
        ]
        middleware.append(
            ToolCallLimitMiddleware(run_limit=int(self.max_tool_calls), exit_behavior="end")
        )
        return middleware
