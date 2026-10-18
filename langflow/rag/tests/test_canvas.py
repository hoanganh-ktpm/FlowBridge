# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Guard the UI contract that Graph.dump alone does not supply."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rag_flow_under_test", ROOT / "flow.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def assert_canvas_connections(definition):
    nodes = {node["id"]: node for node in definition["data"]["nodes"]}
    assert len(nodes) == 3
    assert all(node["type"] == "genericNode" for node in nodes.values())
    edges = definition["data"]["edges"]
    assert len(edges) == 2
    assert len({edge["id"] for edge in edges}) == 2
    for edge in edges:
        for side in ("source", "target"):
            handle_name = side + "Handle"
            handle = json.loads(edge[handle_name].replace("\u0153", '"'))
            assert handle == edge["data"][handle_name]
            assert handle["id"] == edge[side]
        source = nodes[edge["source"]]["data"]
        outputs = source["node"]["outputs"]
        port = edge["data"]["sourceHandle"]["name"]
        if len(outputs) > 1:
            # Selecting the first output (evidence) makes the UI drop the
            # connection from passages, even though the runtime accepts it.
            assert source["selected_output"] == port == "passages"


def test_export_is_importable_on_canvas_and_preserves_runtime_wiring():
    runtime = MODULE.build_graph().dump(endpoint_name="flowbridge-supplier-rag")
    original = [edge["data"] for edge in runtime["data"]["edges"]]
    canvas = MODULE.prepare_canvas(runtime)
    assert_canvas_connections(canvas)
    assert [edge["data"] for edge in canvas["data"]["edges"]] == original
    before = json.dumps(canvas, sort_keys=True)
    assert json.dumps(MODULE.prepare_canvas(canvas), sort_keys=True) == before


def test_shared_template_has_visible_nodes_and_connected_ports():
    template = json.loads((ROOT / "supplier_knowledge.json").read_text(encoding="utf-8"))
    assert_canvas_connections(template)
