# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

# Metadata only. Clone the SAVED ver2, record the exact clone, then change only ver3.
import argparse
import asyncio
import json
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.ver3_nodes import (  # noqa: E402
    AnswerRenderer,
    ConversationState,
    RequestNormalizer,
    RuntimeA2A,
    ServiceBridge,
    WorkflowController,
)
from setup_agent import NOTICE, connect, node  # noqa: E402
from setup_ver2 import ENDPOINTS as V2_ENDPOINTS  # noqa: E402
from setup_ver2 import LangflowAPI, digest, set_field  # noqa: E402

PROJECT = "FlowBridge - ver3"
ENDPOINTS = {k: v.replace("ver2", "ver3") for k, v in V2_ENDPOINTS.items()}
EXPORT_FIELDS = [
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
STATE_FILE = ROOT / "data/ver3-install.json"


def writable(flow: dict) -> dict:
    return {k: deepcopy(flow[k]) for k in EXPORT_FIELDS if k in flow}


def replace_values(value: object, mapping: dict) -> object:
    if isinstance(value, dict):
        return {k: replace_values(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_values(v, mapping) for v in value]
    if isinstance(value, str):
        return mapping.get(value, value)
    return value


def cloned(flow: dict) -> dict:
    value = writable(flow)
    value["name"] = flow["name"].replace("ver2", "ver3")
    value["endpoint_name"] = flow["endpoint_name"].replace("ver2", "ver3")
    return value


def component(cls, suffix: str, x: int, y: int) -> dict:
    stem = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()
    # Use the exact registered component source so the editor offers the correct update.
    code = (ROOT / "components/flowbridge" / ("ver3_" + stem + ".py")).read_text(encoding="utf-8")
    return node(cls, code, cls.__name__ + "-v3-" + suffix, x, y)


async def a2a_from(original: dict) -> dict:
    old = original["data"]["node"]["template"]
    service = old.get("target_service", {}).get("value")
    if service not in {"database", "prediction", "knowledge"}:
        raise ValueError("A2A source must have an explicit service identity")
    replacement = component(RuntimeA2A, service, 0, 1000)
    fields = replacement["data"]["node"]["template"]
    for name in set(fields) & old.keys() - {"code", "_type"}:
        fields[name] = deepcopy(old[name])
    replacement["position"] = deepcopy(original["position"])
    # Native toolkit output metadata comes from the upstream update API.
    instance = RuntimeA2A(_code=fields["code"]["value"])
    frontend = await instance.run_and_validate_update_outputs(
        replacement["data"]["node"], "tool_mode", True
    )
    replacement["data"]["node"] = frontend
    label = original["data"].get("display_name", "A2A").replace("ver2", "ver3")
    replacement["data"]["display_name"] = label
    frontend["display_name"] = label
    set_field(replacement, "input_value", "", "Request do runtime tạo")
    return replacement


async def upgrade(flow: dict, service: str) -> dict:
    result = deepcopy(flow)
    nodes = result["data"]["nodes"]
    incoming = next(n for n in nodes if n["data"]["type"] == "ChatInput")
    outgoing = next(n for n in nodes if n["data"]["type"] == "ChatOutput")
    incoming["position"] = {"x": 0, "y": 0}
    if service != "central":
        # Keep every API adapter including its presets, credentials, code and enabled flag.
        tools = [n for n in nodes if "api_origin" in n["data"]["node"]["template"]]
        if len(tools) != {"database": 5, "prediction": 4, "knowledge": 1}[service]:
            raise ValueError("Unexpected ver2 toolset; inspect before cloning")
        bridge = component(ServiceBridge, service, 650, 60)
        set_field(bridge, "service", service)
        label = (
            "ServiceBridge · "
            + {"database": "Dữ liệu", "prediction": "ML", "knowledge": "RAG"}[service]
        )
        bridge["data"]["display_name"] = label
        bridge["data"]["node"]["display_name"] = label
        outgoing["position"] = {"x": 1150, "y": 60}
        result["data"]["nodes"] = [incoming, bridge, outgoing, *tools]
        result["data"]["edges"] = [
            connect(incoming, "message", bridge, "input_value"),
            connect(bridge, "response", outgoing, "input_value"),
            *[connect(t, "tools", bridge, "tools") for t in tools],
        ]
        set_field(outgoing, "sender_name", "FlowBridge ver3 · " + service)
    else:
        clock = next(n for n in nodes if n["data"]["type"] == "CurrentDate")
        old_agent = next(n for n in nodes if n["data"]["type"] == "Agent")
        tools = [await a2a_from(n) for n in nodes if n["data"]["type"] == "A2AAgent"]
        if len(tools) != 3:
            raise ValueError("Expected three native A2A branches")
        state = component(ConversationState, service, 400, 0)
        gate = component(RequestNormalizer, service, 850, 0)
        # Reuse the current key variable without printing/decrypting its value.
        old_key = old_agent["data"]["node"]["template"].get("api_key", {})
        for key in ("value", "load_from_db"):
            if key in old_key:
                gate["data"]["node"]["template"]["api_key"][key] = deepcopy(old_key[key])
        controller = component(WorkflowController, service, 1320, 0)
        selection = old_agent["data"]["node"]["template"].get("model", {}).get("value")
        if isinstance(selection, list) and selection and selection[0].get("name"):
            set_field(gate, "planner_model", selection[0]["name"])
        renderer = component(AnswerRenderer, service, 1780, 0)
        outgoing["position"] = {"x": 2200, "y": 0}
        clock["position"] = {"x": 0, "y": 420}
        for index, tool in enumerate(tools):
            tool["position"] = {"x": 850 + 430 * index, "y": 900}
        result["data"]["nodes"] = [
            incoming,
            clock,
            state,
            gate,
            controller,
            renderer,
            outgoing,
            *tools,
        ]
        result["data"]["edges"] = [
            connect(incoming, "message", state, "input_value"),
            connect(clock, "current_date", state, "business_time"),
            connect(state, "context", gate, "context"),
            connect(gate, "request", controller, "request"),
            connect(controller, "result", renderer, "result"),
            connect(renderer, "response", outgoing, "input_value"),
            *[
                connect(
                    t,
                    "component_as_tool",
                    controller,
                    t["data"]["node"]["template"]["target_service"]["value"] + "_tools",
                )
                for t in tools
            ],
        ]
        set_field(outgoing, "sender_name", "FlowBridge ver3")
    result["data"]["viewport"] = {"x": 30, "y": 30, "zoom": 0.45}
    result["description"] = (
        "Clone từ ver2 đã lưu; ver3 phân tầng, runtime quản lý hợp đồng/evidence, "
        + (
            "cổng chat 0–2 model requests."
            if service == "central"
            else "API adapters kế thừa, 0 LLM."
        )
    )
    return result


def export(service: str, flow: dict) -> None:
    value = writable(flow)
    # Local snapshots preserve connections. Portable exports never contain literal secrets.
    for item in value["data"]["nodes"]:
        for name, field in item["data"]["node"]["template"].items():
            if not isinstance(field, dict):
                continue
            if (
                field.get("password") or name in {"api_key", "access_token", "ai_token"}
            ) and not field.get("load_from_db"):
                field["value"] = ""
    path = ROOT / "flows/ver3" / (service + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    path.with_suffix(".json.license").write_text(NOTICE, encoding="utf-8", newline="\n")


def all_flows(api: LangflowAPI) -> dict:
    listed = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true")
    if isinstance(listed, dict):
        listed = listed["items"]
    return {f["id"]: api.call("/api/v1/flows/" + f["id"]) for f in listed}


async def install(origin: str) -> None:
    api = LangflowAPI(origin)
    before = all_flows(api)
    sources = {}
    for service, endpoint in V2_ENDPOINTS.items():
        matches = [f for f in before.values() if f.get("endpoint_name") == endpoint]
        if len(matches) != 1:
            raise ValueError("Expected exactly one saved ver2 flow per service")
        sources[service] = matches[0]
    if len({f["folder_id"] for f in sources.values()}) != 1:
        raise ValueError("Ver2 sources are not in the same project")
    if any(f.get("endpoint_name") in ENDPOINTS.values() for f in before.values()):
        raise ValueError(
            "Ver3 already exists; refusing to overwrite it. Use the saved manifest/backup."
        )
    if any(p["name"] == PROJECT for p in api.call("/api/v1/projects/")):
        raise ValueError("Ver3 project already exists; inspect before creating another")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = ROOT / "data" / ("ver3-clone-" + stamp)
    backup.mkdir(parents=True)
    (backup / "ver2-originals.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    project = api.call("/api/v1/projects/", {"name": PROJECT})
    manifest = {
        "version": 3,
        "project_id": project["id"],
        "backup": str(backup),
        "flows": {},
        "source_hashes": {k: digest(f) for k, f in sources.items()},
        "phase": "cloning",
        "openai_requests": 0,
    }
    STATE_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    copies = {}
    for service, source in sources.items():
        value = api.call("/api/v1/flows/", {**cloned(source), "folder_id": project["id"]})
        copies[service] = value
        manifest["flows"][service] = {"id": value["id"], "url": origin + "/flow/" + value["id"]}
        STATE_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    mapping = {
        **{sources[k]["id"]: f["id"] for k, f in copies.items()},
        **{sources[k]["name"]: f["name"] for k, f in copies.items()},
    }
    for service, value in copies.items():
        data = replace_values(writable(value), mapping)
        copies[service] = api.call("/api/v1/flows/" + value["id"], data, "PATCH")
    # This is the reviewable, exact clone before any architecture changes.
    (backup / "ver3-exact-clone.json").write_text(
        json.dumps(copies, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest["phase"] = "upgrading"
    STATE_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for service, value in copies.items():
        revised = await upgrade(writable(value), service)
        saved = api.call("/api/v1/flows/" + value["id"], revised, "PATCH")
        if saved["data"] != revised["data"]:
            raise RuntimeError("Langflow did not preserve ver3 metadata")
        export(service, saved)
        manifest["flows"][service]["applied_hash"] = digest(writable(saved))
    for identifier, value in before.items():
        if digest(api.call("/api/v1/flows/" + identifier)) != digest(value):
            raise RuntimeError("A pre-existing flow changed during cloning; inspect browser drafts")
    manifest.update(phase="installed", protected_flows=len(before))
    STATE_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Installed:", manifest["flows"]["central"]["url"])
    print("Exact clone backup:", backup)
    print("Pre-existing flows unchanged:", len(before), "; OpenAI requests: 0")


async def offline(source_dir: Path) -> None:
    sources = {
        k: json.loads((source_dir / (k + ".json")).read_text(encoding="utf-8")) for k in ENDPOINTS
    }
    mapping = {f["name"]: f["name"].replace("ver2", "ver3") for f in sources.values()}
    for key, value in sources.items():
        export(key, await upgrade(replace_values(cloned(value), mapping), key))
    print("Offline exports generated from", source_dir, "; no network/model calls")


def restore_clone(origin: str) -> None:
    manifest = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    clones = json.loads(
        (Path(manifest["backup"]) / "ver3-exact-clone.json").read_text(encoding="utf-8")
    )
    api = LangflowAPI(origin)
    # Check every target before mutating any target.
    for saved in manifest["flows"].values():
        current = api.call("/api/v1/flows/" + saved["id"])
        if digest(writable(current)) != saved["applied_hash"]:
            raise ValueError("Ver3 was edited after install; export edits before restoring")
    for service, value in clones.items():
        api.call("/api/v1/flows/" + manifest["flows"][service]["id"], writable(value), "PATCH")
    manifest["phase"] = "restored_exact_clone"
    STATE_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Restored ver3 to the exact ver2 clone; ver2 unchanged; no inference")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clone saved ver2, then apply layered ver3; no inference"
    )
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true")
    group.add_argument("--source-dir", type=Path)
    group.add_argument("--restore-clone", action="store_true")
    args = parser.parse_args()
    if args.restore_clone:
        restore_clone(args.origin)
    else:
        asyncio.run(install(args.origin) if args.install else offline(args.source_dir))
