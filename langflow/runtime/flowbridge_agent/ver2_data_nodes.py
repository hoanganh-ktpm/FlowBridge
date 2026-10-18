# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

import json
from copy import deepcopy
from typing import Any, Literal

from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from lfx.custom import Component
from lfx.field_typing import Tool
from lfx.io import (
    BoolInput,
    DropdownInput,
    IntInput,
    MultilineInput,
    Output,
    SecretStrInput,
    StrInput,
)
from lfx.schema.data import Data
from pydantic import Field, ValidationError, create_model

from flowbridge_agent.config import BackendConfig
from flowbridge_agent.nodes import secret_value
from flowbridge_agent.tools import ToolInput, ToolResult, failure, tool_result
from flowbridge_agent.ver2_data import DATA_SPECS, DataBackend


class DataActionTool(BaseTool):
    action: str
    client: DataBackend = Field(exclude=True, repr=False)
    defaults: dict = Field(default_factory=dict, exclude=True)
    locked: dict = Field(default_factory=dict, exclude=True)
    clearable: tuple[str, ...] = ()

    def _run(self, **kwargs: Any):
        raise NotImplementedError("Use asynchronous agent tools")

    async def _arun(self, **kwargs: Any):
        try:
            clear = kwargs.pop("clear_defaults", [])
            if not isinstance(clear, list) or not set(clear) <= set(self.clearable):
                raise ValueError("Invalid clear_defaults")
            if set(kwargs) & self.locked.keys():
                raise ValueError("Locked input")
            values = {**self.defaults, **{k: v for k, v in kwargs.items() if v is not None}}
            values.update({key: None for key in clear})
            values.update(self.locked)
            values = DATA_SPECS[self.action][0].model_validate(values).model_dump(mode="json")
            result = await self.client.execute(self.action, values)
        except (ValidationError, ValueError, TypeError):
            return tool_result(self.action, {}, failure("INVALID_TOOL_ARGUMENTS"))
        return tool_result(self.action, values, result)


def data_inputs(action: str) -> list:
    inputs = [
        BoolInput(name="enabled", display_name="Bật công cụ", value=True),
        MultilineInput(
            name="tool_description", display_name="Mô tả cho agent", value=DATA_SPECS[action][2]
        ),
    ]
    for name, field in DATA_SPECS[action][0].model_fields.items():
        value = "" if field.is_required() or field.default is None else field.default
        labels = {
            "product": "Tên hoặc mã sản phẩm",
            "supplier": "Tên hoặc mã nhà cung cấp",
            "query": "Từ khóa (trống: liệt kê tất cả)",
            "resource": "Danh mục",
            "page": "Trang",
            "page_size": "Số dòng mỗi trang",
            "identifier": "Mã hoặc từ khóa PR/PO (trống: xem lựa chọn)",
        }
        args = dict(name=name, display_name=labels[name], value=value)
        if name == "resource":
            inputs.append(
                DropdownInput(**{**args, "value": "products"}, options=["products", "suppliers"])
            )
        elif name in {"page", "page_size"}:
            inputs.append(IntInput(**args))
        else:
            inputs.append(
                StrInput(**args, info="Để trống cho agent điền theo hội thoại; không cần UUID.")
            )
    inputs.extend(
        [
            MultilineInput(
                name="input_json", display_name="Mặc định JSON", value="{}", advanced=True
            ),
            BoolInput(
                name="lock_inputs", display_name="Khóa mặc định đã điền", value=False, advanced=True
            ),
            StrInput(name="api_origin", display_name="Địa chỉ API", value="", advanced=True),
            SecretStrInput(
                name="access_token", display_name="Access token người dùng", value="", advanced=True
            ),
            IntInput(
                name="timeout", display_name="Thời gian chờ API (giây)", value=150, advanced=True
            ),
            IntInput(
                name="max_pages",
                display_name="Số trang tìm tối đa (100 dòng/trang)",
                value=5,
                advanced=True,
            ),
        ]
    )
    return inputs


class DataNodeBase(Component):
    action = ""
    icon = "Database"
    outputs = [
        Output(name="tools", display_name="Công cụ cho agent", method="get_tools"),
        Output(name="json_result", display_name="Kết quả JSON", method="run_json"),
        Output(name="tool_schema", display_name="Hợp đồng JSON", method="schema_json"),
    ]

    def make_tool(self) -> DataActionTool:
        local = BackendConfig.from_local()
        schema, _, description = DATA_SPECS[self.action]
        values = {
            k: getattr(self, k)
            for k in schema.model_fields
            if getattr(self, k, None) not in (None, "")
        }
        payload = json.loads(self.input_json or "{}")
        if not isinstance(payload, dict) or set(payload) - schema.model_fields.keys():
            raise ValueError("Input JSON must contain only this tool's fields")
        values.update(payload)
        # Validate presets individually; required entity may be supplied later by the agent.
        for key, value in values.items():
            field = deepcopy(schema.model_fields[key])
            create_model(
                "Preset", __base__=ToolInput, **{key: (field.annotation, field)}
            ).model_validate({key: value})
        locked = {k: v for k, v in values.items() if v is not None} if self.lock_inputs else {}
        clearable = tuple(
            k
            for k, v in values.items()
            if v is not None
            and k not in locked
            and not schema.model_fields[k].is_required()
            and schema.model_fields[k].default is None
        )
        fields = {}
        for key, original in schema.model_fields.items():
            if key in locked:
                continue
            field = deepcopy(original)
            annotation = field.annotation
            if key in values:
                annotation = annotation | None
                field.default = None
                field.description = (
                    f"null uses node default: {json.dumps(values[key], ensure_ascii=False)}"
                )
            fields[key] = (annotation, field)
        if clearable:
            fields["clear_defaults"] = (list[Literal[*clearable]], Field(default_factory=list))
        agent_schema = create_model(self.action + "Input", __base__=ToolInput, **fields)
        client = DataBackend(
            BackendConfig(
                origin=str(self.api_origin or local.origin).strip(),
                user_token=secret_value(self.access_token) or local.user_token,
                max_pages=int(self.max_pages),
                timeout=int(self.timeout),
            )
        )
        return DataActionTool(
            name=self.action,
            description=str(self.tool_description or description)
            + (" Locked inputs: " + json.dumps(locked, ensure_ascii=False) if locked else ""),
            args_schema=agent_schema,
            action=self.action,
            client=client,
            defaults=values,
            locked=locked,
            clearable=clearable,
        )

    def get_tools(self) -> list[Tool]:
        return [self.make_tool()] if self.enabled else []

    async def run_json(self) -> Data:
        if not self.enabled:
            return Data(data=tool_result(self.action, {}, failure("TOOL_DISABLED")))
        return Data(data=await self.make_tool()._arun())

    def schema_json(self) -> Data:
        return Data(
            data={
                "tool": convert_to_openai_tool(self.make_tool(), strict=True),
                "output_schema": ToolResult.model_json_schema(),
                "endpoints": [
                    "GET /api/v1/products",
                    "GET /api/v1/suppliers",
                    "GET /api/v1/supplier-products",
                ]
                if self.action.startswith("catalog_")
                else [
                    "GET /api/v1/"
                    + (
                        "purchase-requests"
                        if self.action.endswith("request")
                        else "purchase-orders"
                    ),
                    "GET /api/v1/"
                    + (
                        "purchase-requests/{id}"
                        if self.action.endswith("request")
                        else "purchase-orders/{id}"
                    ),
                ],
                "handoff": "data.resolved.product_id/supplier_id -> ML UUID fields; "
                "choices by code/name only",
            }
        )


class ResolveEntitiesBase(DataNodeBase):
    action = "catalog_resolve_entities"
    display_name = DATA_SPECS[action][1]
    description = DATA_SPECS[action][2]
    inputs = data_inputs(action)


class BrowseCatalogBase(DataNodeBase):
    action = "catalog_list"
    display_name = DATA_SPECS[action][1]
    description = DATA_SPECS[action][2]
    inputs = data_inputs(action)


class ReadOffersBase(DataNodeBase):
    action = "catalog_read_offers"
    display_name = DATA_SPECS[action][1]
    description = DATA_SPECS[action][2]
    inputs = data_inputs(action)


class FindPurchaseRequestBase(DataNodeBase):
    action = "purchase_get_request"
    display_name = DATA_SPECS[action][1]
    description = DATA_SPECS[action][2]
    inputs = data_inputs(action)


class FindPurchaseOrderBase(DataNodeBase):
    action = "purchase_get_order"
    display_name = DATA_SPECS[action][1]
    description = DATA_SPECS[action][2]
    inputs = data_inputs(action)
