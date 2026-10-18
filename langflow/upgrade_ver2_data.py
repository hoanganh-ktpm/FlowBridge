# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

"""Upgrade only ver2 data tools and central routing; never execute an LLM."""

import argparse
import json
from copy import deepcopy
from datetime import datetime

from setup_ver2 import (
    CENTRAL_PROMPT,
    ENDPOINTS,
    ROOT,
    SERVICE_DESCRIPTIONS,
    LangflowAPI,
    connect,
    custom_class,
    digest,
    node,
    specialist_prompt,
)

REPLACEMENTS = [
    ("FlowBridgeFindProducts", "resolve_entities.py", "FlowBridgeResolveEntities"),
    ("FlowBridgeFindSuppliers", "browse_catalog.py", "FlowBridgeBrowseCatalog"),
    ("FlowBridgeSupplierOffers", "read_offers.py", "FlowBridgeReadOffers"),
]
CONNECTION = ("api_origin", "access_token", "timeout", "max_pages")


def data_graph(original: dict) -> dict:
    data = deepcopy(original)
    agent = next(n for n in data["nodes"] if n["data"]["type"] == "Agent")
    template = agent["data"]["node"]["template"]
    template["system_prompt"]["value"] = specialist_prompt("database")
    template["n_messages"]["value"] = 0
    old = {n["data"]["type"]: n for n in data["nodes"]}
    # An already upgraded graph only needs the new prompt; preserve user presets.
    if all(classname in old for _, _, classname in REPLACEMENTS):
        return data
    if not all(kind in old for kind, _, _ in REPLACEMENTS):
        raise ValueError("Data graph tool topology differs; inspect it before migration")
    connections = [
        {k: old[kind]["data"]["node"]["template"][k] for k in CONNECTION}
        for kind, _, _ in REPLACEMENTS
    ]
    # Do not silently combine identities/scopes from differently configured tools.
    if any(value != connections[0] for value in connections[1:]):
        raise ValueError("Old catalog nodes have different connections; reconcile them first")
    replacements = {}
    product_template = old["FlowBridgeFindProducts"]["data"]["node"]["template"]
    supplier_template = old["FlowBridgeFindSuppliers"]["data"]["node"]["template"]
    for kind, filename, classname in REPLACEMENTS:
        before = old[kind]
        fields = before["data"]["node"]["template"]
        if fields.get("output_fields", {}).get("value"):
            raise ValueError("Custom output projection needs manual migration")
        preset = json.loads(fields.get("input_json", {}).get("value") or "{}")
        if preset:
            raise ValueError("Custom input_json needs manual migration")
        cls, code = custom_class(filename, classname)
        after = node(cls, code, classname + "-v2-database", 0, 0)
        after["position"] = deepcopy(before["position"])
        new_fields = after["data"]["node"]["template"]
        for key in (*CONNECTION, "enabled", "lock_inputs"):
            if key in fields:
                new_fields[key] = deepcopy(fields[key])
        if kind == "FlowBridgeFindProducts":
            for target, source in (("product", product_template), ("supplier", supplier_template)):
                new_fields[target]["value"] = source.get("query", {}).get("value", "")
            if bool(product_template["lock_inputs"]["value"]) != bool(
                supplier_template["lock_inputs"]["value"]
            ):
                raise ValueError("Different lookup input locks need manual migration")
        elif kind == "FlowBridgeFindSuppliers" and fields.get("query", {}).get("value"):
            new_fields["resource"]["value"] = "suppliers"
            new_fields["query"]["value"] = fields["query"]["value"]
        elif kind == "FlowBridgeSupplierOffers":
            for key in ("product", "supplier"):
                new_fields[key]["value"] = fields.get(key + "_id", {}).get("value", "")
        replacements[before["id"]] = after
    data["nodes"] = [replacements.get(n["id"], n) for n in data["nodes"]]
    data["edges"] = [
        e
        for e in data["edges"]
        if e["source"] not in replacements and e["target"] not in replacements
    ]
    data["edges"].extend(connect(n, "tools", agent, "tools") for n in replacements.values())
    return data


def central_graph(original: dict) -> dict:
    data = deepcopy(original)
    for n in data["nodes"]:
        fields = n["data"]["node"]["template"]
        if n["data"]["type"] == "Agent":
            fields["system_prompt"]["value"] = CENTRAL_PROMPT
            fields["n_messages"]["value"] = max(12, int(fields["n_messages"]["value"]))
        if n["data"]["type"] == "A2AAgent":
            for action in fields.get("tools_metadata", {}).get("value", []):
                if action.get("name") == "hoi_du_lieu":
                    action["description"] = SERVICE_DESCRIPTIONS["database"]
                    n["data"]["description"] = SERVICE_DESCRIPTIONS["database"]
                    n["data"]["node"]["description"] = SERVICE_DESCRIPTIONS["database"]
    return data


def upgrade(origin: str, apply: bool) -> None:
    api = LangflowAPI(origin)
    listed = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true")
    if isinstance(listed, dict):
        listed = listed["items"]
    snapshots = {f["id"]: api.call("/api/v1/flows/" + f["id"]) for f in listed}
    targets = [
        f
        for f in snapshots.values()
        if f.get("endpoint_name") in {ENDPOINTS["database"], ENDPOINTS["central"]}
    ]
    if len(targets) != 2 or len({f.get("folder_id") for f in targets}) != 1:
        raise ValueError("Expected exactly one data and central ver2 flow in the same project")
    plans = []
    for flow in targets:
        is_data = flow["endpoint_name"] == ENDPOINTS["database"]
        data = data_graph(flow["data"]) if is_data else central_graph(flow["data"])
        payload = {"data": data}
        if is_data:
            payload.update(description=SERVICE_DESCRIPTIONS["database"])
            card = deepcopy(flow.get("a2a_card_overrides") or {})
            card["description"] = SERVICE_DESCRIPTIONS["database"]
            payload["a2a_card_overrides"] = card
        plans.append((flow, payload))
        print("Prepared:", flow["endpoint_name"], len(data["nodes"]), "nodes")
    if not apply:
        print("Dry run: no metadata changed. Use --apply to install.")
        return
    backup = (
        ROOT / "data" / ("ver2-before-data-upgrade-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    )
    backup.mkdir()
    for flow, _ in plans:
        (backup / (flow["id"] + ".json")).write_text(
            json.dumps(flow, ensure_ascii=False), encoding="utf-8"
        )
    # Keep local-test disconnect bookkeeping valid after tool IDs change.
    state_file = ROOT / "data/local-test-connection.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
    if state:
        (backup / "local-test-connection.json").write_text(json.dumps(state), encoding="utf-8")
    for flow, payload in plans:
        # Refuse to overwrite edits made after this migration prepared its snapshot.
        if digest(api.call("/api/v1/flows/" + flow["id"])) != digest(flow):
            raise RuntimeError("Flow was edited during migration; rerun with the latest metadata")
        saved = api.call("/api/v1/flows/" + flow["id"], payload, "PATCH")
        if saved["data"] != payload["data"]:
            raise RuntimeError("Langflow did not preserve requested graph")
        if state and flow["endpoint_name"] == ENDPOINTS["database"]:
            originals = state.get("flows", {}).get(flow["id"], {})
            for old_kind, _, new_kind in REPLACEMENTS:
                old_id, new_id = old_kind + "-v2-database", new_kind + "-v2-database"
                if old_id in originals:
                    originals[new_id] = originals.pop(old_id)
        print("Updated:", flow["endpoint_name"])
    if state:
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    target_ids = {f["id"] for f in targets}
    for identifier, before in snapshots.items():
        if identifier not in target_ids and digest(
            api.call("/api/v1/flows/" + identifier)
        ) != digest(before):
            raise RuntimeError("Unrelated flow changed during migration; inspect browser drafts")
    print("Backups:", backup)
    print("Connections/model credentials preserved. OpenAI requests: 0; workflow runs: 0.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    upgrade(args.origin, args.apply)
