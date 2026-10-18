# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

# Repair saved component identity/edges only. Never execute the graph or call a model.
import argparse
import asyncio
import json
from copy import deepcopy
from datetime import datetime

from setup_agent import connect
from setup_ver2 import LangflowAPI, digest
from setup_ver3 import (
    ROOT,
    STATE_FILE,
    WorkflowController,
    a2a_from,
    component,
    export,
    writable,
)


async def repair(origin: str) -> None:
    manifest = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    api = LangflowAPI(origin)
    listed = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true")
    if isinstance(listed, dict):
        listed = listed["items"]
    before = {f["id"]: api.call("/api/v1/flows/" + f["id"]) for f in listed}
    identifier = manifest["flows"]["central"]["id"]
    current = before[identifier]
    if current["folder_id"] != manifest["project_id"]:
        raise ValueError("Unexpected ver3 project; refusing repair")
    value = writable(current)
    data = value["data"]
    controller = next(n for n in data["nodes"] if n["id"] == "WorkflowController-v3-central")
    if (
        "from flowbridge_agent.ver3_nodes import WorkflowController as NativeComponent"
        not in controller["data"]["node"]["template"]["code"]["value"]
    ):
        raise ValueError("Controller code was edited; refusing overwrite")
    fresh_controller = component(WorkflowController, "central", 0, 0)
    fresh_fields = fresh_controller["data"]["node"]["template"]
    old_fields = controller["data"]["node"]["template"]
    # Only the three service input ports change; keep every shared input preset.
    for name in fresh_fields.keys() & old_fields.keys() - {"code", "_type"}:
        fresh_fields[name] = deepcopy(old_fields[name])
    controller["data"]["node"] = fresh_controller["data"]["node"]
    old_nodes = [n for n in data["nodes"] if n["id"].startswith(("A2AAgent-v2-", "RuntimeA2A-v3-"))]
    if len(old_nodes) != 3:
        raise ValueError("Expected exactly three ver3 A2A nodes")
    changed = {}
    services = set()
    for original in old_nodes:
        fields = original["data"]["node"]["template"]
        service = original["id"].rsplit("-", 1)[-1]
        if service not in {"database", "prediction", "knowledge"} or service in services:
            raise ValueError("Ambiguous service identity")
        services.add(service)
        target_id = manifest["flows"][service]["id"]
        target = before[target_id]
        if (
            fields["mode"]["value"] != "Internal"
            or fields["agent_name_selected"]["value"] != target["name"]
        ):
            raise ValueError("A2A target was edited; refusing to redirect it")
        prepared = deepcopy(original)
        template = prepared["data"]["node"]["template"]
        template["target_service"] = {"value": service}
        template["agent_name_selected"].update(
            options=[target["name"]], options_metadata=[{"id": target_id}]
        )
        changed[original["id"]] = await a2a_from(prepared)
    data["nodes"] = [changed.get(n["id"], n) for n in data["nodes"]]
    # The generated wrapper must match the registered library source. Preserve all presets.
    wrappers = {
        "ConversationState": "conversation_state",
        "RequestNormalizer": "request_normalizer",
        "WorkflowController": "workflow_controller",
        "AnswerRenderer": "answer_renderer",
    }
    for item in data["nodes"]:
        kind = item["data"]["type"]
        if kind in wrappers:
            code = item["data"]["node"]["template"]["code"]["value"]
            if f"from flowbridge_agent.ver3_nodes import {kind} as NativeComponent" not in code:
                raise ValueError("Custom node code was edited; refusing overwrite")
            item["data"]["node"]["template"]["code"]["value"] = (
                ROOT / "components/flowbridge" / ("ver3_" + wrappers[kind] + ".py")
            ).read_text(encoding="utf-8")
    edges = []
    for edge in data["edges"]:
        if edge["source"] in changed:
            if edge["target"] != controller["id"] or edge["data"]["targetHandle"][
                "fieldName"
            ] not in {"tools", "database_tools", "prediction_tools", "knowledge_tools"}:
                raise ValueError("Unexpected A2A wiring; inspect before repair")
            replacement = changed[edge["source"]]
            role = replacement["data"]["node"]["template"]["target_service"]["value"]
            edges.append(connect(replacement, "component_as_tool", controller, role + "_tools"))
        else:
            edges.append(edge)
    data["edges"] = edges
    service_ids = {node["id"] for node in changed.values()}
    service_ports = [
        edge["data"]["targetHandle"]["fieldName"]
        for edge in edges
        if edge["source"] in service_ids and edge["target"] == controller["id"]
    ]
    if sorted(service_ports) != ["database_tools", "knowledge_tools", "prediction_tools"]:
        raise ValueError("Each service must have exactly one explicit controller connection")
    backup = (
        ROOT
        / "data"
        / ("ver3-before-route-fix-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    )
    backup.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    if digest(api.call("/api/v1/flows/" + identifier)) != digest(current):
        raise RuntimeError("Canvas changed while preparing repair; no update applied")
    saved = api.call("/api/v1/flows/" + identifier, value, "PATCH")
    if saved["data"] != data:
        raise RuntimeError("Langflow did not preserve the repaired graph")
    for flow_id, snapshot in before.items():
        if flow_id != identifier and digest(api.call("/api/v1/flows/" + flow_id)) != digest(
            snapshot
        ):
            raise RuntimeError("Another flow changed during repair; inspect drafts")
    export("central", saved)
    manifest["flows"]["central"]["applied_hash"] = digest(writable(saved))
    manifest["route_fix_backup"] = str(backup)
    STATE_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Repaired ver3 A2A: three explicit service ports; preserved other presets/flows.")
    print("No graph execution; no model/API business calls. Backup:", backup)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Repair ver3 A2A identity after native editor refresh"
    )
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    args = parser.parse_args()
    asyncio.run(repair(args.origin))
