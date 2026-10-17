# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import argparse
import gzip
import importlib.util
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.io_contracts import EXAMPLES  # noqa: E402
from flowbridge_agent.prompt import SYSTEM_PROMPT  # noqa: E402
from flowbridge_agent.tools import validate_arguments  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402
from lfx.components.input_output.chat import ChatInput  # noqa: E402
from lfx.components.input_output.chat_output import ChatOutput  # noqa: E402
from lfx.custom.utils import build_custom_component_template  # noqa: E402

NOTICE = "SPDX-License-Identifier: GPL-3.0-or-later\nFlowBridge; see LICENSE.\n"


def custom_class(filename: str, name: str):
    path = ROOT / "components/flowbridge" / filename
    spec = importlib.util.spec_from_file_location(
        "flowbridge_" + filename.removesuffix(".py"), path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name), path.read_text(encoding="utf-8")


def node(cls, code: str, identifier: str, x: int, y: int) -> dict:
    frontend, _ = build_custom_component_template(cls(_code=code))
    if cls is ChatInput or cls is ChatOutput:
        incoming = cls is ChatInput
        frontend["display_name"] = "Nhập câu hỏi" if incoming else "Trả lời người dùng"
        frontend["description"] = (
            "Nhận câu hỏi tự nhiên từ Playground và chuyển cho trợ lý."
            if incoming
            else "Hiển thị câu trả lời trợ lý đã tổng hợp từ các công cụ."
        )
        frontend["template"]["input_value"]["display_name"] = (
            "Câu hỏi" if incoming else "Câu trả lời từ trợ lý"
        )
        frontend["outputs"][0]["display_name"] = "Câu hỏi" if incoming else "Câu trả lời"
    return {
        "id": identifier,
        "type": "genericNode",
        "position": {"x": x, "y": y},
        "data": {
            "id": identifier,
            "type": identifier.split("-")[0],
            "node": frontend,
            "display_name": frontend["display_name"],
            "description": frontend["description"],
        },
    }


def connect(source: dict, output: str, target: dict, field: str) -> dict:
    descriptor = next(o for o in source["data"]["node"]["outputs"] if o["name"] == output)
    target_field = target["data"]["node"]["template"][field]
    types = descriptor["types"]
    source_handle = {
        "dataType": source["data"]["type"],
        "id": source["id"],
        "name": output,
        "output_types": types,
    }
    target_handle = {
        "id": target["id"],
        "fieldName": field,
        "inputTypes": target_field.get("input_types", []),
        "type": target_field["type"],
    }
    return {
        "id": f"{source['id']}-{output}-{target['id']}-{field}",
        "source": source["id"],
        "target": target["id"],
        "animated": False,
        "sourceHandle": json.dumps(source_handle, separators=(",", ":")).replace('"', "œ"),
        "targetHandle": json.dumps(target_handle, separators=(",", ":")).replace('"', "œ"),
        "data": {"sourceHandle": source_handle, "targetHandle": target_handle},
    }


def make_flow() -> dict:
    # Reference upstream components through imports instead of vendoring their source.
    notice = "# SPDX-License-Identifier: GPL-3.0-or-later\n# FlowBridge; see LICENSE.\n"
    chat_in = node(
        ChatInput,
        notice + "from lfx.components.input_output.chat import ChatInput as ChatComponent\n"
        "class ChatInput(ChatComponent):\n    pass\n",
        "ChatInput-procurement",
        2200,
        1050,
    )
    chat_out = node(
        ChatOutput,
        notice + "from lfx.components.input_output.chat_output import ChatOutput as ChatComponent\n"
        "class ChatOutput(ChatComponent):\n    pass\n",
        "ChatOutput-procurement",
        3150,
        1500,
    )
    nodes = [chat_in, chat_out]
    mapping = {}
    contracts = {}
    definitions = [
        ("agent.py", "FlowBridgeProcurementAgent", 2650, 1500),
        ("luna.py", "FlowBridgeLunaModel", 2650, 300),
        ("catalog.py", "FlowBridgeCatalogTools", 2050, 350),
        ("prediction.py", "FlowBridgePredictionTools", 2050, 1800),
        ("knowledge.py", "FlowBridgeKnowledgeTools", 2050, 3100),
        ("find_products.py", "FlowBridgeFindProducts", 0, 0),
        ("find_suppliers.py", "FlowBridgeFindSuppliers", 400, 0),
        ("supplier_offers.py", "FlowBridgeSupplierOffers", 800, 0),
        ("purchase_request.py", "FlowBridgePurchaseRequest", 1200, 0),
        ("purchase_order.py", "FlowBridgePurchaseOrder", 1600, 0),
        ("rank_suppliers.py", "FlowBridgeRankSuppliers", 400, 1400),
        ("score_supplier.py", "FlowBridgeScoreSupplier", 800, 1400),
        ("order_risk.py", "FlowBridgeOrderRisk", 1200, 1400),
        ("prediction_result.py", "FlowBridgePredictionResult", 1600, 1400),
        ("retrieve_knowledge.py", "FlowBridgeRetrieveKnowledge", 1600, 2900),
    ]
    for filename, name, x, y in definitions:
        cls, code = custom_class(filename, name)
        if getattr(cls, "action", ""):
            # Validate configuration/schema only; never execute a tool or a model.
            component = cls(_code=code)
            definition = component.schema_json().data
            Draft202012Validator.check_schema(definition["output_schema"])
            Draft202012Validator.check_schema(definition["success_data_schema"])
            validate_arguments(cls.action, EXAMPLES[cls.action])
            contracts[cls.action] = definition
        item = node(cls, code, name + "-procurement", x, y)
        nodes.append(item)
        mapping[name] = item
    agent = mapping["FlowBridgeProcurementAgent"]
    template = agent["data"]["node"]["template"]
    template["system_prompt"]["value"] = SYSTEM_PROMPT
    template["model"]["value"] = None
    template["model"]["input_types"] = ["LanguageModel"]
    template["max_iterations"]["value"] = 4
    template["max_tool_calls"]["value"] = 3
    template["max_tokens"]["value"] = 600
    template["n_messages"]["value"] = 6
    edges = [
        connect(chat_in, "message", agent, "input_value"),
        connect(agent, "response", chat_out, "input_value"),
        connect(mapping["FlowBridgeLunaModel"], "model", agent, "model"),
    ]
    groups = {
        "FlowBridgeCatalogTools": [
            "FlowBridgeFindProducts",
            "FlowBridgeFindSuppliers",
            "FlowBridgeSupplierOffers",
            "FlowBridgePurchaseRequest",
            "FlowBridgePurchaseOrder",
        ],
        "FlowBridgePredictionTools": [
            "FlowBridgeRankSuppliers",
            "FlowBridgeScoreSupplier",
            "FlowBridgeOrderRisk",
            "FlowBridgePredictionResult",
        ],
        "FlowBridgeKnowledgeTools": ["FlowBridgeRetrieveKnowledge"],
    }
    for service, children in groups.items():
        parent = mapping[service]
        for child in children:
            edges.append(connect(mapping[child], "tools", parent, "child_tools"))
        edges.append(connect(parent, "tools", agent, "tools"))
    contract_path = ROOT / "flows/node-contracts.json"
    contract_path.parent.mkdir(exist_ok=True)
    contract_path.write_text(
        json.dumps(contracts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    contract_path.with_suffix(".json.license").write_text(NOTICE, encoding="utf-8")
    return {
        "name": "FlowBridge - Trợ lý mua hàng",
        "is_component": False,
        "description": "Trợ lý điều phối dữ liệu, dự đoán và tri thức; 10 công cụ có schema JSON, "
        "mặc định/khóa tham số và mô tả chuyển dữ liệu. Mặc định 4 lần model/3 công cụ mỗi lượt.",
        "endpoint_name": "flowbridge-procurement",
        "data": {
            "nodes": nodes,
            "edges": edges,
            "viewport": {"x": 40, "y": 40, "zoom": 0.35},
        },
    }


def install(flow: dict, origin: str) -> str:
    token = ""

    def api(path: str, body=None, method=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(
            origin + path,
            headers=headers,
            method=method,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        )
        try:
            with urlopen(request, timeout=180) as response:
                payload = response.read()
                if payload.startswith(b"\x1f\x8b"):
                    payload = gzip.decompress(payload)
                return json.loads(payload)
        except HTTPError as exc:
            raise RuntimeError(
                f"Langflow API HTTP {exc.code}; no OpenAI request was made"
            ) from None

    token = api("/api/v1/auto_login")["access_token"]
    existing = api("/api/v1/flows/?get_all=true")
    if isinstance(existing, dict):
        existing = existing.get("items", [])
    match = next(
        (
            f
            for f in existing
            if f.get("endpoint_name") == flow["endpoint_name"]
            or f.get("name") in {flow["name"], "FlowBridge - Procurement Agent"}
        ),
        None,
    )
    if match:
        backup = ROOT / "data/procurement-before-vietnamese-routing.json"
        if not backup.exists():
            previous = api("/api/v1/flows/" + match["id"])
            backup.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
        saved = api("/api/v1/flows/" + match["id"], flow, method="PATCH")
    else:
        saved = api("/api/v1/flows/", flow)
    path = ROOT / "data/agent-install.json"
    path.write_text(
        json.dumps({"id": saved["id"], "url": origin + "/flow/" + saved["id"]}), encoding="utf-8"
    )
    return origin + "/flow/" + saved["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/import a Langflow graph; never runs OpenAI")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    options = parser.parse_args()
    flow = make_flow()
    output = ROOT / "flows/procurement-agent.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(flow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Built flow:", output)
    if options.install:
        print("Installed:", install(flow, options.origin))
    print("OpenAI requests: 0")


if __name__ == "__main__":
    main()
