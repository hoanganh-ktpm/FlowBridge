# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

"""Insert protocol nodes and rewire ver2, preserving every existing node byte for byte."""

import argparse
import json
from copy import deepcopy
from datetime import datetime

from setup_ver2 import ENDPOINTS, ROOT, LangflowAPI, connect, custom_class, digest, native, node


def insert_protocol_nodes(flow: dict, role: str) -> dict:
    from lfx.components.processing.converter import TypeConverterComponent

    data = deepcopy(flow["data"])
    old_nodes = deepcopy(data["nodes"])
    if any(n["id"].startswith(("A2AProtocolAdapter-", "A2AReplyEnvelope-")) for n in old_nodes):
        raise ValueError("Protocol nodes already exist; refusing duplicate insertion")
    row = max(n["position"]["y"] for n in old_nodes) + 600
    agent = next(n for n in old_nodes if n["data"]["type"] == "Agent")
    routes = [n for n in old_nodes if n["data"]["type"] == "A2AAgent"]
    for index, source in enumerate(routes):
        fields = source["data"]["node"]["template"]
        target = fields["target_service"]["value"]
        if fields["caller_service"]["value"] != role or fields["mode"]["value"] != "Internal":
            raise ValueError("Unexpected existing A2A route")
        cls, code = custom_class("a2_a_protocol_adapter.py", "A2AProtocolAdapter")
        adapter = node(cls, code, f"A2AProtocolAdapter-ver2-{role}-{target}", index * 460, row)
        template = adapter["data"]["node"]["template"]
        template["caller_service"]["value"] = role
        template["target_service"]["value"] = target
        adapter["data"]["node"]["display_name"] = f"Chuẩn hóa A2A · {role} → {target}"
        matches = [
            e
            for e in data["edges"]
            if e["source"] == source["id"]
            and e["target"] == agent["id"]
            and e["data"]["targetHandle"]["fieldName"] == "tools"
        ]
        if len(matches) != 1:
            raise ValueError("Expected one existing A2A -> Agent tools edge")
        old_edge = matches[0]
        data["edges"].remove(old_edge)
        data["nodes"].append(adapter)
        data["edges"].extend(
            [
                connect(source, old_edge["data"]["sourceHandle"]["name"], adapter, "source_tools"),
                connect(adapter, "tools", agent, "tools"),
            ]
        )
    if role != "central":
        output = next(n for n in old_nodes if n["data"]["type"] == "ChatOutput")
        matches = [e for e in data["edges"] if e["target"] == output["id"]]
        if len(matches) != 1:
            raise ValueError("Expected one existing Parser -> Chat Output edge")
        old_edge = matches[0]
        parser = next(n for n in old_nodes if n["id"] == old_edge["source"])
        if parser["data"]["type"] not in {"Parser", "ParserComponent"}:
            raise ValueError("Unexpected existing output path")
        cls, code = custom_class("a2_a_reply_envelope.py", "A2AReplyEnvelope")
        envelope = node(cls, code, f"A2AReplyEnvelope-ver2-{role}", 0, row + 420)
        envelope["data"]["node"]["template"]["service"]["value"] = role
        converter = native(
            TypeConverterComponent,
            "lfx.components.processing.converter",
            "TypeConverterComponent",
            "protocol-" + role,
            "Type Convert · JSON A2A → Message",
            460,
            row + 420,
        )
        data["nodes"].extend([envelope, converter])
        data["edges"].remove(old_edge)
        data["edges"].extend(
            [
                connect(parser, old_edge["data"]["sourceHandle"]["name"], envelope, "input_value"),
                connect(envelope, "envelope", converter, "input_data"),
                connect(converter, "message_output", output, "input_value"),
            ]
        )
    assert_existing_nodes(flow["data"], data)
    return data


def assert_existing_nodes(before: dict, after: dict) -> None:
    saved = {n["id"]: n for n in after["nodes"]}
    if len(saved) != len(after["nodes"]):
        raise ValueError("Duplicate node ID")
    if any(saved.get(n["id"]) != n for n in before["nodes"]):
        raise ValueError("An existing node was modified")
    for edge in after["edges"]:
        if edge["source"] not in saved or edge["target"] not in saved:
            raise ValueError("Dangling edge")


def apply(origin: str, write: bool) -> None:
    from lfx.graph.graph.base import Graph

    api = LangflowAPI(origin)
    listed = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true")
    listed = listed.get("items", []) if isinstance(listed, dict) else listed
    before = {f["id"]: api.call("/api/v1/flows/" + f["id"]) for f in listed}
    plans = {}
    for role, endpoint in ENDPOINTS.items():
        found = [f for f in before.values() if f.get("endpoint_name") == endpoint]
        if len(found) != 1:
            raise ValueError("Expected four unambiguous ver2 flows")
        flow = found[0]
        plans[role] = (flow, insert_protocol_nodes(flow, role))
        graph = Graph.from_payload(deepcopy(plans[role][1]), user_id=flow["user_id"])
        graph.topological_sort()
    if len({f["folder_id"] for f, _ in plans.values()}) != 1:
        raise ValueError("ver2 flows must be in the same project")
    if not write:
        print("Prepared 11 additional nodes; every existing node preserved. Use --apply.")
        return
    backup = (
        ROOT / "data" / ("ver2-before-protocol-nodes-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    )
    backup.mkdir()
    for role, (flow, _) in plans.items():
        (backup / (role + ".json")).write_text(
            json.dumps(flow, ensure_ascii=False), encoding="utf-8"
        )
    applied = []
    try:
        for flow, data in plans.values():
            path = "/api/v1/flows/" + flow["id"]
            if digest(api.call(path)) != digest(flow):
                raise RuntimeError("Concurrent edit; refusing stale overwrite")
            applied.append(flow)
            saved = api.call(path, {"data": data}, "PATCH")
            if saved["data"] != data:
                raise RuntimeError("Saved graph differs from prepared graph")
            assert_existing_nodes(flow["data"], saved["data"])
    except Exception:
        for flow in reversed(applied):
            api.call("/api/v1/flows/" + flow["id"], {"data": flow["data"]}, "PATCH")
        raise
    targets = {flow["id"] for flow, _ in plans.values()}
    for identifier, original in before.items():
        if identifier not in targets and digest(api.call("/api/v1/flows/" + identifier)) != digest(
            original
        ):
            raise RuntimeError("An unrelated flow changed during insertion")
    # Apply the same additive graph change to portable exports, retaining their presets.
    for role in plans:
        path = ROOT / "flows/ver2" / (role + ".json")
        export = json.loads(path.read_text(encoding="utf-8"))
        export["data"] = insert_protocol_nodes(export, role)
        path.write_text(
            json.dumps(export, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    manifest = {
        role: {
            "flow_id": flow["id"],
            "existing_nodes": len(flow["data"]["nodes"]),
            "added_nodes": len(data["nodes"]) - len(flow["data"]["nodes"]),
            "existing_node_hashes": {n["id"]: digest(n) for n in flow["data"]["nodes"]},
        }
        for role, (flow, data) in plans.items()
    }
    (backup / "preservation.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Applied 11 nodes. Existing nodes/presets/prompts/models/credentials preserved.")
    print("Backup:", backup)
    print("Model/workflow calls: 0.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    apply(args.origin, args.apply)
