# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import argparse
import asyncio
import gzip
import hashlib
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.config import local_values  # noqa: E402
from flowbridge_agent.ver2_prompts import (  # noqa: E402
    CENTRAL_PROMPT,
    INPUT_TEMPLATE,
    SERVICE_DESCRIPTIONS,
    output_table,
    specialist_prompt,
)
from lfx.components.input_output.chat import ChatInput  # noqa: E402
from lfx.components.input_output.chat_output import ChatOutput  # noqa: E402
from lfx.components.models_and_agents.a2a_agent import A2AAgentComponent  # noqa: E402
from lfx.components.models_and_agents.agent import AgentComponent  # noqa: E402
from lfx.components.models_and_agents.prompt import PromptComponent  # noqa: E402
from lfx.components.processing.operations import OperationsComponent  # noqa: E402
from lfx.components.processing.parser import ParserComponent  # noqa: E402
from lfx.components.utilities.current_date import CurrentDateComponent  # noqa: E402
from setup_agent import NOTICE, connect, custom_class, node  # noqa: E402

PROJECT = "FlowBridge - ver2"
KEY_VARIABLE = "FLOWBRIDGE_VER2_OPENAI_API_KEY"
NAMES = {
    "database": PROJECT + " - Dữ liệu",
    "prediction": PROJECT + " - Dự đoán ML",
    "knowledge": PROJECT + " - Tri thức RAG",
    "central": PROJECT,
}
ENDPOINTS = {key: "flowbridge-ver2-" + key for key in NAMES}
TOOLS = {
    "database": [
        ("find_products.py", "FlowBridgeFindProducts"),
        ("find_suppliers.py", "FlowBridgeFindSuppliers"),
        ("supplier_offers.py", "FlowBridgeSupplierOffers"),
        ("purchase_request.py", "FlowBridgePurchaseRequest"),
        ("purchase_order.py", "FlowBridgePurchaseOrder"),
    ],
    "prediction": [
        ("rank_suppliers.py", "FlowBridgeRankSuppliers"),
        ("score_supplier.py", "FlowBridgeScoreSupplier"),
        ("order_risk.py", "FlowBridgeOrderRisk"),
        ("prediction_result.py", "FlowBridgePredictionResult"),
    ],
    "knowledge": [("retrieve_knowledge.py", "FlowBridgeRetrieveKnowledge")],
}
SLUGS = {"database": "hoi_du_lieu", "prediction": "hoi_du_doan", "knowledge": "hoi_tri_thuc"}


def native(cls, module: str, kind: str, suffix: str, label: str, x: int, y: int) -> dict:
    # Import upstream code; keep the native component identity and runtime methods.
    code = (
        "# SPDX-License-Identifier: GPL-3.0-or-later\n# FlowBridge; see LICENSE.\n"
        f"from {module} import {cls.__name__} as NativeComponent\n"
        f"class {cls.__name__}(NativeComponent):\n    pass\n"
    )
    item = node(cls, code, kind + "-v2-" + suffix, x, y)
    item["data"]["display_name"] = label
    item["data"]["node"]["display_name"] = label
    descriptions = {
        "Agent": "Agent chuẩn Langflow: chọn model, chỉnh prompt và dùng các công cụ đã nối.",
        "CurrentDate": "Lấy ngày giờ theo múi giờ đã chọn, không gọi model.",
        "Prompt": "Ghép ngày giờ nghiệp vụ với câu hỏi hoặc JSON A2A, không gọi model.",
        "Parser": "Chuyển JSON từ Structured Response thành thông điệp để trả qua A2A.",
        "Operations": "Chỉ bỏ khung Markdown bên ngoài; giữ nội dung JSON, không sửa dữ liệu.",
    }
    if kind in descriptions:
        item["data"]["description"] = descriptions[kind]
        item["data"]["node"]["description"] = descriptions[kind]
    return item


def set_field(item: dict, name: str, value, label: str | None = None, *, advanced=None) -> None:
    field = item["data"]["node"]["template"][name]
    field["value"] = value
    if label:
        field["display_name"] = label
    if advanced is not None:
        field["advanced"] = advanced


def base_flow(service: str) -> tuple[dict, dict]:
    central = service == "central"
    agent_label = {
        "central": "Agent trung tâm",
        "database": "Agent Dữ liệu",
        "prediction": "Agent Dự đoán ML",
        "knowledge": "Agent Tri thức RAG",
    }[service]
    incoming = native(
        ChatInput,
        "lfx.components.input_output.chat",
        "ChatInput",
        service,
        "Nhập câu hỏi" if central else "Nhận yêu cầu A2A",
        0,
        0,
    )
    clock = native(
        CurrentDateComponent,
        "lfx.components.utilities.current_date",
        "CurrentDate",
        service,
        "Ngày giờ Việt Nam",
        0,
        460,
    )
    prompt = native(
        PromptComponent,
        "lfx.components.models_and_agents.prompt",
        "Prompt",
        service,
        "Ghép ngữ cảnh và yêu cầu",
        410,
        70,
    )
    agent = native(
        AgentComponent,
        "lfx.components.models_and_agents.agent",
        "Agent",
        service,
        agent_label,
        850,
        70,
    )
    outgoing = native(
        ChatOutput,
        "lfx.components.input_output.chat_output",
        "ChatOutput",
        service,
        "Trả lời người dùng" if central else "Trả JSON cho trung tâm",
        2110 if not central else 1290,
        70,
    )
    set_field(incoming, "should_store_message", central, "Lưu câu hỏi vào lịch sử", advanced=True)
    set_field(clock, "timezone", "Asia/Ho_Chi_Minh", "Múi giờ")
    clock["data"]["node"]["template"]["timezone"]["options"] = ["Asia/Ho_Chi_Minh", "UTC"]
    set_field(prompt, "template", INPUT_TEMPLATE, "Mẫu ngữ cảnh")
    PromptComponent()._update_template(prompt["data"]["node"])
    for name, label in [
        ("business_time", "Thời gian nghiệp vụ"),
        ("question", "Câu hỏi / JSON A2A"),
    ]:
        prompt["data"]["node"]["template"][name]["display_name"] = label
    set_field(
        agent,
        "system_prompt",
        CENTRAL_PROMPT if central else specialist_prompt(service),
        "System prompt / chỉ dẫn agent",
    )
    selection = {
        "name": "gpt-5.6-luna",
        "provider": "OpenAI",
        "category": "OpenAI",
        "metadata": {"reasoning": True, "tool_calling": True, "model_class": "ChatOpenAI"},
    }
    set_field(agent, "model", [selection], "Chọn mô hình")
    agent["data"]["node"]["template"]["model"]["options"] = [selection]
    set_field(agent, "api_key", KEY_VARIABLE, "API key / biến bí mật", advanced=False)
    agent["data"]["node"]["template"]["api_key"]["show"] = True
    set_field(
        agent,
        "max_iterations",
        4 if service in {"central", "database"} else 2,
        "Giới hạn lượt model",
        advanced=False,
    )
    set_field(
        agent, "max_tokens", 6000 if not central else 1600, "Giới hạn output token", advanced=False
    )
    set_field(agent, "n_messages", 6 if central else 0, "Số tin nhắn lịch sử", advanced=False)
    for field in [
        "add_current_date_tool",
        "add_calculator_tool",
        "handle_parsing_errors",
        "stream",
    ]:
        set_field(agent, field, False)
    set_field(agent, "input_value", "", "Yêu cầu đã ghép ngữ cảnh")
    agent["data"]["node"]["template"]["tools"]["display_name"] = "Các công cụ được phép"
    set_field(outgoing, "should_store_message", central, "Lưu câu trả lời", advanced=True)
    set_field(outgoing, "sender_name", agent_label)
    nodes = [incoming, clock, prompt, agent, outgoing]
    edges = [
        connect(incoming, "message", prompt, "question"),
        connect(clock, "current_date", prompt, "business_time"),
        connect(prompt, "prompt", agent, "input_value"),
    ]
    if central:
        edges.append(connect(agent, "response", outgoing, "input_value"))
    else:
        # Langflow's canvas selects ports through node.data, not Output.selected (a type).
        agent["data"]["selected_output"] = "structured_response"
        set_field(agent, "output_schema", output_table(), "Schema JSON trả về", advanced=False)
        set_field(
            agent,
            "format_instructions",
            "Trả đúng một record service trong objects. Giữ nguyên ToolResult trong results; "
            "không thêm markdown, không dựng kết quả, mọi list trống là [].",
        )
        parser = native(
            ParserComponent,
            "lfx.components.processing.parser",
            "Parser",
            service,
            "JSON → thông điệp A2A",
            1270,
            70,
        )
        set_field(parser, "mode", "Stringify", "Chế độ")
        set_field(parser, "pattern", "", advanced=True)
        parser["data"]["node"]["template"]["pattern"].update(show=False, required=False)
        cleaner = native(
            OperationsComponent,
            "lfx.components.processing.operations",
            "Operations",
            service,
            "Bỏ khung Markdown, giữ JSON",
            1690,
            70,
        )
        set_field(cleaner, "input_type", "Text", "Loại input")
        set_field(cleaner, "operation", [{"name": "Text Replace", "icon": "replace"}], "Phép xử lý")
        set_field(cleaner, "use_regex", True, "Dùng regex")
        set_field(cleaner, "search_pattern", r"\A```json\s*\n|\n```\s*\Z", "Chỉ bỏ khung ngoài")
        set_field(cleaner, "replacement_text", "", "Thay bằng")
        for name in ["use_regex", "search_pattern", "replacement_text"]:
            cleaner["data"]["node"]["template"][name]["show"] = True
        nodes += [parser, cleaner]
        edges += [
            connect(agent, "structured_response", parser, "input_data"),
            connect(parser, "parsed_text", cleaner, "text_input"),
            connect(cleaner, "message_output", outgoing, "input_value"),
        ]
    flow = {
        "name": NAMES[service],
        "description": (
            "Agent trung tâm giao việc cho ba agent chuyên trách bằng A2A Internal; "
            "model và prompt chỉnh được. Bản độc lập, không sửa workflow cũ."
            if central
            else SERVICE_DESCRIPTIONS[service]
        ),
        "endpoint_name": ENDPOINTS[service],
        "is_component": False,
        "flow_type": "agent",
        "a2a_enabled": not central,
        "access_type": "PRIVATE",
        "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 30, "y": 30, "zoom": 0.4}},
    }
    if not central:
        flow["a2a_card_overrides"] = {
            "name": NAMES[service],
            "description": SERVICE_DESCRIPTIONS[service],
            "tags": ["flowbridge", "ver2", service],
        }
    return flow, agent


def specialist_flow(service: str) -> dict:
    flow, agent = base_flow(service)
    for index, (filename, classname) in enumerate(TOOLS[service]):
        cls, code = custom_class(filename, classname)
        item = node(cls, code, classname + "-v2-" + service, index * 410, 1000)
        # Each copied node keeps its editable defaults and contract-backed API adapter.
        cls(_code=code).schema_json()
        flow["data"]["nodes"].append(item)
        flow["data"]["edges"].append(connect(item, "tools", agent, "tools"))
    return flow


async def central_flow(children: dict[str, dict]) -> dict:
    flow, agent = base_flow("central")
    for index, (service, child) in enumerate(children.items()):
        item = native(
            A2AAgentComponent,
            "lfx.components.models_and_agents.a2a_agent",
            "A2AAgent",
            service,
            "A2A · " + NAMES[service].split(" - ")[-1],
            410 * index,
            1000,
        )
        set_field(item, "mode", "Internal", "Kiểu kết nối")
        set_field(item, "agent_name_selected", child["name"], "Agent chuyên trách")
        pick = item["data"]["node"]["template"]["agent_name_selected"]
        pick.update(show=True, options=[child["name"]], options_metadata=[{"id": child["id"]}])
        for name in ["agent_url", "api_key", "timeout", "agent_card"]:
            item["data"]["node"]["template"][name].update(show=False, required=False)
        set_field(item, "input_value", "", "JSON yêu cầu (agent tự điền)")
        item["data"]["description"] = SERVICE_DESCRIPTIONS[service]
        item["data"]["node"]["description"] = SERVICE_DESCRIPTIONS[service]
        component = A2AAgentComponent(_code=item["data"]["node"]["template"]["code"]["value"])
        frontend = await component.run_and_validate_update_outputs(
            item["data"]["node"], "tool_mode", True
        )
        action = frontend["template"]["tools_metadata"]["value"][0]
        action.update(name=SLUGS[service], description=SERVICE_DESCRIPTIONS[service])
        frontend["template"]["tools_metadata"]["display_name"] = "Tên và mô tả công cụ A2A"
        item["data"]["node"] = frontend
        flow["data"]["nodes"].append(item)
        flow["data"]["edges"].append(connect(item, "component_as_tool", agent, "tools"))
    return flow


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class LangflowAPI:
    def __init__(self, origin: str):
        self.origin = origin.rstrip("/")
        self.token = ""
        self.token = self.call("/api/v1/auto_login")["access_token"]

    def call(self, path: str, body=None, method=None):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = Request(
            self.origin + path,
            headers=headers,
            method=method,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        )
        try:
            with urlopen(request, timeout=180) as response:
                raw = response.read()
                return json.loads(gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw)
        except HTTPError as exc:
            raise RuntimeError(
                f"Langflow metadata API {path}: HTTP {exc.code}; response body hidden"
            ) from None

    def install(self, flow: dict, project_id: str, existing: list[dict]) -> dict:
        matches = [f for f in existing if f.get("endpoint_name") == flow["endpoint_name"]]
        if len(matches) > 1:
            raise ValueError("Duplicate ver2 endpoints; refusing ambiguous update")
        payload = {**flow, "folder_id": project_id}
        if matches:
            found = matches[0]
            if found.get("folder_id") != project_id or not found["name"].startswith(PROJECT):
                raise ValueError("Endpoint belongs to another project; refusing overwrite")
            return self.call("/api/v1/flows/" + found["id"], payload, "PATCH")
        return self.call("/api/v1/flows/", payload, "POST")


def write_export(service: str, flow: dict) -> None:
    target = ROOT / "flows/ver2" / (service + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(flow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target.with_suffix(".json.license").write_text(NOTICE, encoding="utf-8")


async def build(install: bool, origin: str) -> None:
    children = {service: specialist_flow(service) for service in TOOLS}
    api = LangflowAPI(origin) if install else None
    existing = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true") if api else []
    if isinstance(existing, dict):
        existing = existing.get("items", [])
    protected = (
        {
            f["id"]: api.call("/api/v1/flows/" + f["id"])
            for f in existing
            if f.get("endpoint_name") not in ENDPOINTS.values()
        }
        if api
        else {}
    )
    if api:
        projects = api.call("/api/v1/projects/")
        candidates = [p for p in projects if p["name"] == PROJECT]
        if len(candidates) > 1:
            raise ValueError("Duplicate ver2 project name; refusing ambiguous install")
        project = candidates[0] if candidates else api.call("/api/v1/projects/", {"name": PROJECT})
        # A dedicated credential name avoids modifying providers/credentials used by v1.
        variables = api.call("/api/v1/variables/")
        if not any(v["name"] == KEY_VARIABLE for v in variables):
            key = local_values().get("OPENAI_API_KEY")
            if key:
                api.call(
                    "/api/v1/variables/",
                    {
                        "name": KEY_VARIABLE,
                        "value": key,
                        "type": "Credential",
                        "default_fields": [],
                    },
                )
        for service, flow in children.items():
            children[service] = api.install(flow, project["id"], existing)
    else:
        # Placeholder ids are export-only: --install resolves actual IDs before building central.
        from uuid import NAMESPACE_URL, uuid5

        children = {
            key: {**value, "id": str(uuid5(NAMESPACE_URL, ENDPOINTS[key]))}
            for key, value in children.items()
        }
    main_flow = await central_flow(children)
    if api:
        main_flow = api.install(main_flow, project["id"], existing)
        # Verify every pre-existing unrelated flow was preserved byte-for-byte as JSON.
        for identifier, before in protected.items():
            if digest(api.call("/api/v1/flows/" + identifier)) != digest(before):
                raise RuntimeError(
                    "A protected flow changed during install; inspect local browser drafts"
                )
        proof = {
            "project_id": project["id"],
            "flows": {
                key: {"id": f["id"], "name": f["name"], "url": origin + "/flow/" + f["id"]}
                for key, f in {**children, "central": main_flow}.items()
            },
            "protected": {key: digest(value) for key, value in protected.items()},
            "openai_requests": 0,
            "business_api_requests": 0,
        }
        (ROOT / "data/ver2-install.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Installed:", proof["flows"]["central"]["url"])
        print("Unchanged existing flows:", len(protected))
    for service, flow in {**children, "central": main_flow}.items():
        # Source exports contain reproducible configuration, not local owner/project metadata.
        export = {
            k: flow[k]
            for k in [
                "name",
                "description",
                "endpoint_name",
                "is_component",
                "flow_type",
                "a2a_enabled",
                "access_type",
                "data",
                "a2a_card_overrides",
            ]
            if k in flow
        }
        write_export(service, export)
        print(service, len(flow["data"]["nodes"]), "nodes", len(flow["data"]["edges"]), "edges")
    print("OpenAI requests: 0; business API requests: 0. No inference was executed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create isolated native A2A workflows; never execute inference"
    )
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    options = parser.parse_args()
    asyncio.run(build(options.install, options.origin))
