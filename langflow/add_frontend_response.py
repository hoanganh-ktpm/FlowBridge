# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare a ver2 JSON-output branch offline, preserving every existing node/edge.

No live Langflow updates, provider calls or database access. Output is a separate
export for review; never silently overwrite a flow being edited by the team.
"""

import argparse
import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT.parent / "backend")]
NODE_ID = "FrontendResponse-ver2-central"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def prepare(flow: dict) -> dict:
    from lfx.custom.utils import build_custom_component_template

    before = flow["data"]
    if any(n["id"] == NODE_ID for n in before["nodes"]):
        raise ValueError("FRONTEND_BRANCH_ALREADY_EXISTS")
    agents = [n for n in before["nodes"] if n["data"]["type"] == "Agent"]
    if len(agents) != 1:
        raise ValueError("EXPECTED_ONE_CENTRAL_VER2_AGENT")
    agent = agents[0]
    # Explicit graph shape: one agent with its existing direct chat output edge.
    outputs = {n["id"] for n in before["nodes"] if n["data"]["type"] == "ChatOutput"}
    if not any(e["source"] == agent["id"] and e["target"] in outputs for e in before["edges"]):
        raise ValueError("NOT_A_CENTRAL_CHAT_FLOW")
    code_path = ROOT / "components/flowbridge/frontend_response.py"
    spec = importlib.util.spec_from_file_location("frontend_response_component", code_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    template, _ = build_custom_component_template(
        module.FrontendResponse(_code=code_path.read_text("utf-8"))
    )
    target = {
        "id": NODE_ID,
        "type": "genericNode",
        "position": {
            "x": agent.get("position", {}).get("x", 0) + 600,
            "y": agent.get("position", {}).get("y", 0) + 650,
        },
        "data": {
            "id": NODE_ID,
            "type": "FrontendResponse",
            "node": template,
            "display_name": template["display_name"],
            "description": template["description"],
        },
    }
    descriptor = next(o for o in agent["data"]["node"]["outputs"] if o["name"] == "response")
    if descriptor["types"] != ["Message"]:
        raise ValueError("EXPECTED_NATIVE_AGENT_MESSAGE")
    source_handle = {
        "dataType": "Agent",
        "id": agent["id"],
        "name": "response",
        "output_types": ["Message"],
    }
    field = template["template"]["agent_message"]
    target_handle = {
        "id": NODE_ID,
        "fieldName": "agent_message",
        "inputTypes": field["input_types"],
        "type": field["type"],
    }
    edge = {
        "id": f"{agent['id']}-response-{NODE_ID}-agent_message",
        "source": agent["id"],
        "target": NODE_ID,
        "animated": False,
        "sourceHandle": json.dumps(source_handle, separators=(",", ":")).replace('"', "œ"),
        "targetHandle": json.dumps(target_handle, separators=(",", ":")).replace('"', "œ"),
        "data": {"sourceHandle": source_handle, "targetHandle": target_handle},
    }
    result = deepcopy(flow)
    result["data"]["nodes"].append(target)
    result["data"]["edges"].append(edge)
    assert_preserved(flow, result)
    return result


def assert_preserved(before, after):
    old, new = before["data"], after["data"]
    mapping = {n["id"]: n for n in new["nodes"]}
    if len(mapping) != len(new["nodes"]):
        raise ValueError("DUPLICATE_NODE_ID")
    if any(mapping.get(n["id"]) != n for n in old["nodes"]):
        raise ValueError("EXISTING_NODE_CHANGED")
    if new["edges"][: len(old["edges"])] != old["edges"]:
        raise ValueError("EXISTING_EDGE_CHANGED")
    if len(new["nodes"]) != len(old["nodes"]) + 1 or len(new["edges"]) != len(old["edges"]) + 1:
        raise ValueError("UNEXPECTED_GRAPH_CHANGE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="Older ver2 export without the JSON branch"
    )
    parser.add_argument("--output", type=Path, help="New export file; never an existing flow/file")
    args = parser.parse_args()
    source = json.loads(args.source.read_text("utf-8"))
    result = prepare(source)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(
        json.dumps(
            {
                "new_node": NODE_ID,
                "new_edges": 1,
                "existing_nodes_unchanged": True,
                "existing_edges_unchanged": True,
                "live_flow_updated": False,
                "original_nodes_sha256": digest(source["data"]["nodes"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
