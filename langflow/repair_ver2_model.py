# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import argparse
import inspect
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from lfx.components.models_and_agents.agent import AgentComponent
from setup_ver2 import (
    ENDPOINTS,
    ROOT,
    LangflowAPI,
    OpenAICompatibleAgent,
    digest,
    native,
)


def repair(origin: str) -> None:
    api = LangflowAPI(origin)
    flows = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true")
    if isinstance(flows, dict):
        flows = flows["items"]
    snapshots = {item["id"]: api.call("/api/v1/flows/" + item["id"]) for item in flows}
    targets = [f for f in snapshots.values() if f.get("endpoint_name") in ENDPOINTS.values()]
    if len(targets) != 4:
        raise ValueError("Expected exactly four ver2 workflows; refusing an ambiguous repair")
    backup = ROOT / "data" / ("ver2-before-model-fix-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir()
    plans = []
    for flow in targets:
        data = deepcopy(flow["data"])
        agents = [n for n in data["nodes"] if n["data"]["type"] == "Agent"]
        if len(agents) != 1:
            raise ValueError("Expected one native Agent per ver2 flow")
        agent = agents[0]
        frontend = agent["data"]["node"]
        template = frontend["template"]
        code = template["code"]["value"]
        stock_code = Path(inspect.getfile(AgentComponent)).read_text(encoding="utf-8")
        stock_match = code.strip() == stock_code.strip()
        if not stock_match and ("as NativeComponent" not in code or "pass" not in code):
            raise ValueError("Agent code was customized; preserve it and inspect before repairing")
        replacement = native(
            OpenAICompatibleAgent,
            "flowbridge_agent.ver2_agent",
            "Agent",
            "repair",
            frontend["display_name"],
            0,
            0,
        )["data"]["node"]
        template["code"] = replacement["template"]["code"]
        for name in [
            "openai_api_mode",
            "openai_reasoning_effort",
            "openai_sdk_retries",
            "openai_timeout",
        ]:
            template.setdefault(name, deepcopy(replacement["template"][name]))
        frontend["metadata"] = replacement["metadata"]
        frontend["edited"] = True
        plans.append((flow, data))
        # Backups stay ignored/local, including any private presets; never export them to source.
        (backup / (flow["id"] + ".json")).write_text(
            json.dumps(flow, ensure_ascii=False), encoding="utf-8"
        )
    for flow, data in plans:
        saved = api.call("/api/v1/flows/" + flow["id"], {"data": data}, "PATCH")
        assert saved["data"] == data
        print("Updated model configuration:", flow["name"])
    protected = {f["id"]: f for f in snapshots.values() if f not in targets}
    for identifier, before in protected.items():
        assert digest(api.call("/api/v1/flows/" + identifier)) == digest(before)
    print("Other workflows unchanged:", len(protected))
    print("OpenAI requests: 0. No workflow or business API was executed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repair ver2 model options; metadata only")
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    repair(parser.parse_args().origin)
