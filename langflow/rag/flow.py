# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Chat Input -> authenticated supplier retrieval -> cited passages; no ML or LLM calls."""

import importlib.util
import json
import os
import sys
from pathlib import Path

from lfx.components.input_output import ChatInput, ChatOutput
from lfx.graph import Graph
from lfx.graph.flow_builder.connect import add_connection


def prepare_canvas(definition: dict) -> dict:
    """Complete Graph.dump's runtime graph with Langflow's ReactFlow metadata."""
    for node in definition["data"]["nodes"]:
        node["type"] = "genericNode"
        node["data"]["showNode"] = True
    # The runtime reads data.*Handle, whereas the canvas also needs serialized
    # handles and stable edge IDs. The upstream builder supplies both and pins
    # the selected output on components with an output dropdown.
    connections = definition["data"]["edges"]
    definition["data"]["edges"] = []
    for edge in connections:
        add_connection(
            definition,
            edge["source"],
            edge["data"]["sourceHandle"]["name"],
            edge["target"],
            edge["data"]["targetHandle"]["fieldName"],
        )
    return definition


def build_graph(*, task_id: str = "", supplier_id: str = "", log_config=None) -> Graph:
    source = Path(__file__).resolve().parents[1] / "components/supplier_rag/retrieve_knowledge.py"
    module_name = "flowbridge_supplier_rag"
    spec = importlib.util.spec_from_file_location(module_name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    question = ChatInput().set(should_store_message=True)
    retrieval = module.RetrieveSupplierKnowledge(_code=source.read_text(encoding="utf-8")).set(
        query=question.message_response,
        analysis_task_id=task_id,
        supplier_id=supplier_id,
        top_k=3,
    )
    output = ChatOutput().set(
        input_value=retrieval.retrieve_message,
        should_store_message=True,
    )
    return Graph(
        question,
        output,
        flow_name="FlowBridge - Supplier RAG",
        description="Retrieve supplier review passages and versioned citations from PostgreSQL.",
        log_config=log_config,
    )


def get_graph(log_config=None) -> Graph:
    return build_graph(
        task_id=os.environ.get("FLOWBRIDGE_RAG_TASK_ID", ""),
        supplier_id=os.environ.get("FLOWBRIDGE_RAG_SUPPLIER_ID", ""),
        log_config=log_config,
    )


if __name__ == "__main__":
    definition = prepare_canvas(build_graph().dump(endpoint_name="flowbridge-supplier-rag"))
    positions = {
        "ChatInput": {"x": 0, "y": 100},
        "FlowBridgeRetrieveKnowledge": {"x": 380, "y": 100},
        "ChatOutput": {"x": 780, "y": 100},
    }
    for node in definition["data"]["nodes"]:
        node["position"] = positions[node["data"]["type"]]
    destination = Path(__file__).with_name("supplier_knowledge.json")
    destination.write_text(
        json.dumps(definition, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("Exported the RAG flow template with blank task/supplier and no credentials.")
