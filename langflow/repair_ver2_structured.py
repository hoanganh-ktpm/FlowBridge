# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import argparse
import json
from copy import deepcopy
from datetime import datetime

from setup_ver2 import ENDPOINTS, ROOT, LangflowAPI, StructuredServiceAgent, digest, native


def repair(origin: str) -> None:
    api = LangflowAPI(origin)
    listed = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true")
    if isinstance(listed, dict):
        listed = listed["items"]
    snapshots = {f["id"]: api.call("/api/v1/flows/" + f["id"]) for f in listed}
    endpoints = {ENDPOINTS[key] for key in ["database", "prediction", "knowledge"]}
    targets = [f for f in snapshots.values() if f.get("endpoint_name") in endpoints]
    if len(targets) != 3:
        raise ValueError("Expected exactly three ver2 specialist flows")
    backup = (
        ROOT / "data" / ("ver2-before-structured-fix-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    )
    backup.mkdir()
    replacement = native(
        StructuredServiceAgent,
        "flowbridge_agent.ver2_structured",
        "Agent",
        "repair",
        "Agent",
        0,
        0,
    )["data"]["node"]
    plans = []
    for flow in targets:
        data = deepcopy(flow["data"])
        agents = [n for n in data["nodes"] if n["data"]["type"] == "Agent"]
        if len(agents) != 1:
            raise ValueError("Expected one Agent per specialist flow")
        frontend = agents[0]["data"]["node"]
        code = frontend["template"]["code"]["value"]
        if "flowbridge_agent.ver2_coordination_nodes" in code:
            print("Coordination already retains the structured adapter:", flow["id"])
            continue
        if not any(
            module in code
            for module in [
                "from flowbridge_agent.ver2_agent import OpenAICompatibleAgent as NativeComponent",
                "from flowbridge_agent.ver2_structured import "
                "StructuredServiceAgent as NativeComponent",
            ]
        ):
            raise ValueError("Agent code is customized; inspect before replacing it")
        frontend["template"]["code"] = deepcopy(replacement["template"]["code"])
        frontend["metadata"] = deepcopy(replacement["metadata"])
        frontend["edited"] = True
        (backup / (flow["id"] + ".json")).write_text(
            json.dumps(flow, ensure_ascii=False), encoding="utf-8"
        )
        plans.append((flow, data))
    for flow, data in plans:
        saved = api.call("/api/v1/flows/" + flow["id"], {"data": data}, "PATCH")
        if saved["data"] != data:
            raise RuntimeError("Langflow did not preserve the requested configuration")
        print("Updated specialist structured output:", flow["id"])
    for identifier, flow in snapshots.items():
        if flow not in targets and digest(api.call("/api/v1/flows/" + identifier)) != digest(flow):
            raise RuntimeError("Another workflow changed; inspect browser drafts")
    print("All presets/prompts/schema/edges/connections preserved. OpenAI requests: 0.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repair ver2 structured output; metadata only")
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    repair(parser.parse_args().origin)
