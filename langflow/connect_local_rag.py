# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Only connect the RAG tool; never import defaults over customized ver2 data/ML flows.
import argparse
import hashlib
import json
from copy import deepcopy

from setup_ver2 import ENDPOINTS, ROOT, LangflowAPI, digest

ORIGIN = "http://127.0.0.1:8001"
STATE = ROOT / "data/local-rag-connection.json"
FIELDS = ("api_origin", "access_token", "job_context", "enabled")


def connect(origin: str, *, disconnect: bool = False) -> None:
    profile = json.loads((ROOT / "data/local-test.json").read_text(encoding="utf-8"))
    if profile.get("origin") != ORIGIN or not all(
        profile.get(k) for k in ["ai_user_id", "job_context"]
    ):
        raise ValueError("Run scripts/prepare_langflow_rag.py first")
    credential = (
        "FLOWBRIDGE_LOCAL_TEST_TOKEN_" + hashlib.sha256(profile["token"].encode()).hexdigest()[:12]
    )
    api = LangflowAPI(origin)
    listed = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true")
    if isinstance(listed, dict):
        listed = listed["items"]
    snapshots = {f["id"]: api.call("/api/v1/flows/" + f["id"]) for f in listed}
    targets = [f for f in snapshots.values() if f.get("endpoint_name") == ENDPOINTS["knowledge"]]
    if len(targets) != 1:
        raise ValueError("Expected exactly one ver2 knowledge flow")
    flow = targets[0]
    data = deepcopy(flow["data"])
    nodes = [
        n
        for n in data["nodes"]
        if n["data"]["node"]["template"].get("tool_name", {}).get("value")
        == "knowledge_search_supplier"
    ]
    if len(nodes) != 1:
        raise ValueError("Expected exactly one RAG tool")
    node = nodes[0]
    fields = node["data"]["node"]["template"]
    if not all(k in fields for k in FIELDS):
        raise ValueError("RAG tool configuration is incomplete")
    saved = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None
    if disconnect:
        if not saved or not saved["active"]:
            print("Local RAG is already disconnected.")
            return
        if saved["flow_id"] != flow["id"] or saved["node_id"] != node["id"]:
            raise ValueError("RAG node changed since connection; refusing to overwrite")
        if any(fields[k] != saved["applied"][k] for k in FIELDS):
            raise ValueError("RAG connection was edited; refusing to overwrite")
        for name in FIELDS:
            fields[name] = deepcopy(saved["before"][name])
    else:
        if not saved or not saved["active"]:
            saved = {
                "flow_id": flow["id"],
                "node_id": node["id"],
                "active": True,
                "before": {name: deepcopy(fields[name]) for name in FIELDS},
            }
        fields["api_origin"]["value"] = ORIGIN
        fields["access_token"].update(value=credential, load_from_db=True)
        fields["job_context"]["value"] = profile["job_context"]
        fields["enabled"]["value"] = True
        variables = api.call("/api/v1/variables/")
        matches = [v for v in variables if v["name"] == credential]
        if matches and (len(matches) != 1 or matches[0]["type"] != "Credential"):
            raise ValueError("Local credential name is used with another type")
        if not matches:
            api.call(
                "/api/v1/variables/",
                {
                    "name": credential,
                    "value": profile["token"],
                    "type": "Credential",
                    "default_fields": [],
                },
            )
        saved["applied"] = {name: deepcopy(fields[name]) for name in FIELDS}
        STATE.write_text(
            json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    result = api.call("/api/v1/flows/" + flow["id"], {"data": data}, "PATCH")
    if result["data"] != data:
        raise RuntimeError("Langflow did not preserve the RAG connection")
    for identifier, previous in snapshots.items():
        if identifier != flow["id"] and digest(api.call("/api/v1/flows/" + identifier)) != digest(
            previous
        ):
            raise RuntimeError("Another workflow changed concurrently; inspect browser drafts")
    saved["active"] = not disconnect
    STATE.write_text(
        json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print("Ver2 RAG " + ("disconnected." if disconnect else "connected to PostgreSQL test8001."))
    print("Other flows/presets/prompts/edges unchanged. OpenAI requests: 0.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Connect ver2 RAG to local token + real DB JobContext"
    )
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    parser.add_argument("--disconnect", action="store_true")
    args = parser.parse_args()
    connect(args.origin, disconnect=args.disconnect)
