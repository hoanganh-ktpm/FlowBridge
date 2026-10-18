# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

"""Upgrade ver2's native A2A graph without running a workflow or model."""

import argparse
import asyncio
import json
from copy import deepcopy
from datetime import datetime

from setup_ver2 import (
    CENTRAL_PROMPT,
    ENDPOINTS,
    ROOT,
    SERVICE_DESCRIPTIONS,
    BoundedA2AAgent,
    CoordinatedCentralAgent,
    CoordinatedServiceAgent,
    LangflowAPI,
    a2a_node,
    connect,
    custom_class,
    digest,
    native,
    node,
    output_table,
    specialist_prompt,
    write_export,
)

MODULE = "flowbridge_agent.ver2_coordination_nodes"


def replace_code(item: dict, cls) -> None:
    frontend = item["data"]["node"]
    code = frontend["template"]["code"]["value"]
    if "as NativeComponent" not in code or "pass" not in code:
        raise ValueError("Custom node code needs inspection before coordination migration")
    replacement = native(
        cls, MODULE, item["data"]["type"], "upgrade", frontend["display_name"], 0, 0
    )
    fresh = replacement["data"]["node"]
    frontend["template"]["code"] = deepcopy(fresh["template"]["code"])
    frontend["metadata"] = deepcopy(fresh["metadata"])
    frontend["edited"] = True
    for key, field in fresh["template"].items():
        frontend["template"].setdefault(key, deepcopy(field))
    if "field_order" in frontend:
        frontend["field_order"] += [
            k for k in fresh.get("field_order", []) if k not in frontend["field_order"]
        ]


async def coordinated_graph(flow: dict, role: str, targets: dict) -> dict:
    data = deepcopy(flow["data"])
    agents = [n for n in data["nodes"] if n["data"]["type"] == "Agent"]
    if len(agents) != 1:
        raise ValueError("Expected one Agent in each ver2 flow")
    agent = agents[0]
    if role == "database":
        for item in data["nodes"]:
            replacements = {
                "FlowBridgePurchaseRequest": (
                    "find_purchase_request.py",
                    "FlowBridgeFindPurchaseRequest",
                ),
                "FlowBridgePurchaseOrder": (
                    "find_purchase_order.py",
                    "FlowBridgeFindPurchaseOrder",
                ),
            }
            if item["data"]["type"] not in replacements:
                continue
            fields = item["data"]["node"]["template"]
            if fields.get("output_fields", {}).get("value"):
                raise ValueError("Custom purchase output projection needs manual migration")
            cls, code = custom_class(*replacements[item["data"]["type"]])
            fresh = node(cls, code, item["id"], 0, 0)
            new_fields = fresh["data"]["node"]["template"]
            for key in (
                "identifier",
                "input_json",
                "enabled",
                "lock_inputs",
                "api_origin",
                "access_token",
                "timeout",
                "max_pages",
            ):
                if key in fields:
                    new_fields[key]["value"] = deepcopy(fields[key]["value"])
            fresh["position"] = item["position"]
            item.update(fresh)
    central = role == "central"
    replace_code(agent, CoordinatedCentralAgent if central else CoordinatedServiceAgent)
    fields = agent["data"]["node"]["template"]
    fields["system_prompt"]["value"] = CENTRAL_PROMPT if central else specialist_prompt(role)
    fields["n_messages"]["value"] = max(12, int(fields["n_messages"]["value"])) if central else 0
    if not central:
        fields["coordination_role"]["value"] = role
        fields["output_schema"]["value"] = output_table()
        fields["format_instructions"]["value"] = (
            "Trả đúng một record trong objects: service,status,resolved,results,missing,"
            "questions,warnings,handoffs. results=[]; runtime tự đính kèm chứng cứ từ tool, "
            "không chép lại ToolResult. Các list rỗng là []."
        )
    if role in {"prediction", "knowledge"}:
        fields["max_iterations"]["value"] = max(3, int(fields["max_iterations"]["value"]))
    # SDK retries can issue paid requests underneath model middleware; disable retries here.
    fields["openai_sdk_retries"]["value"] = 0
    for item in data["nodes"]:
        if item["data"]["type"] != "A2AAgent":
            continue
        template = item["data"]["node"]["template"]
        if template["mode"]["value"] != "Internal":
            raise ValueError("Only Internal A2A is supported by this coordination design")
        actions = template["tools_metadata"]["value"]
        if len(actions) != 1:
            raise ValueError("Expected exactly one A2A action")
        action = actions[0]
        roles = {
            "hoi_du_lieu": "database",
            "hoi_du_doan": "prediction",
            "hoi_tri_thuc": "knowledge",
        }
        pick = template["agent_name_selected"]
        options = pick.get("options", [])
        metadata = pick.get("options_metadata", [])
        selected_id = None
        if pick["value"] in options:
            index = options.index(pick["value"])
            if index < len(metadata):
                selected_id = str(metadata[index].get("id") or "")
        matches = [
            key
            for key, target_flow in targets.items()
            if key != "central"
            and (
                str(target_flow["id"]) == selected_id
                if selected_id
                else target_flow["name"] == pick["value"]
            )
        ]
        target = matches[0] if len(matches) == 1 else None
        if target is None or (not central and (role == "database" or target != "database")):
            raise ValueError(
                f"Existing A2A action {action['name']!r} on {item['id']} "
                "conflicts with the acyclic coordination graph"
            )
        action["name"] = next(slug for slug, service in roles.items() if service == target)
        replace_code(item, BoundedA2AAgent)
        template = item["data"]["node"]["template"]
        template["caller_service"]["value"] = role
        template["target_service"]["value"] = target
        action["description"] = SERVICE_DESCRIPTIONS[target]
        item["data"]["description"] = SERVICE_DESCRIPTIONS[target]
        item["data"]["node"]["description"] = SERVICE_DESCRIPTIONS[target]
    if role in {"prediction", "knowledge"} and not any(
        n["data"]["type"] == "A2AAgent" for n in data["nodes"]
    ):
        item = await a2a_node(
            "database", targets["database"], role, 4 if role == "prediction" else 1
        )
        item["data"]["node"]["edited"] = True
        data["nodes"].append(item)
        data["edges"].append(connect(item, "component_as_tool", agent, "tools"))
    # Native Parser.Stringify emits a JSON fence. Decode it once at the A2A
    # boundary; the old Operations dynamic UI reset its regex fields at runtime.
    for cleaner in [n for n in data["nodes"] if n["data"]["type"] == "Operations"]:
        if cleaner["id"] != "Operations-v2-" + role:
            raise ValueError("Unexpected Operations node; inspect before removing it")
        incoming = [e for e in data["edges"] if e["target"] == cleaner["id"]]
        outgoing = [e for e in data["edges"] if e["source"] == cleaner["id"]]
        if len(incoming) != 1 or len(outgoing) != 1:
            raise ValueError("Unexpected transport cleaner connections")
        parser = next(n for n in data["nodes"] if n["id"] == incoming[0]["source"])
        output = next(n for n in data["nodes"] if n["id"] == outgoing[0]["target"])
        if (
            parser["data"]["type"] not in {"Parser", "ParserComponent"}
            or output["data"]["type"] != "ChatOutput"
        ):
            raise ValueError("Transport cleaner is outside the expected response path")
        data["nodes"].remove(cleaner)
        data["edges"] = [e for e in data["edges"] if e not in incoming + outgoing]
        data["edges"].append(connect(parser, "parsed_text", output, "input_value"))
    return data


async def upgrade(origin: str, apply: bool) -> None:
    api = LangflowAPI(origin)
    listed = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true")
    if isinstance(listed, dict):
        listed = listed["items"]
    snapshots = {f["id"]: api.call("/api/v1/flows/" + f["id"]) for f in listed}
    targets = {}
    for role, endpoint in ENDPOINTS.items():
        found = [f for f in snapshots.values() if f.get("endpoint_name") == endpoint]
        if len(found) != 1:
            raise ValueError("Expected exactly four unambiguous ver2 flows")
        targets[role] = found[0]
    if len({f.get("folder_id") for f in targets.values()}) != 1:
        raise ValueError("ver2 flows must belong to the same project")
    plans = []
    for role, flow in targets.items():
        data = await coordinated_graph(flow, role, targets)
        payload = {"data": data}
        if role != "central":
            card = deepcopy(flow.get("a2a_card_overrides") or {})
            card["description"] = SERVICE_DESCRIPTIONS[role]
            payload.update(description=SERVICE_DESCRIPTIONS[role], a2a_card_overrides=card)
        plans.append((flow, payload))
        print("Prepared", flow["endpoint_name"], len(data["nodes"]), "nodes")
    if not apply:
        print("Dry run: no metadata changed; use --apply.")
        return
    backup = (
        ROOT / "data" / ("ver2-before-coordination-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    )
    backup.mkdir()
    for flow, _ in plans:
        (backup / (flow["id"] + ".json")).write_text(
            json.dumps(flow, ensure_ascii=False), encoding="utf-8"
        )
    applied = []
    try:
        for flow, payload in plans:
            if digest(api.call("/api/v1/flows/" + flow["id"])) != digest(flow):
                raise RuntimeError("Flow was edited while preparing; refusing stale overwrite")
            applied.append(flow)
            saved = api.call("/api/v1/flows/" + flow["id"], payload, "PATCH")
            if saved["data"] != payload["data"]:
                raise RuntimeError("Saved graph differs from the prepared graph")
    except Exception:
        for flow in reversed(applied):
            api.call(
                "/api/v1/flows/" + flow["id"],
                {
                    key: flow[key]
                    for key in ["data", "description", "a2a_card_overrides"]
                    if key in flow
                },
                "PATCH",
            )
        raise
    target_ids = {f["id"] for f in targets.values()}
    for identifier, flow in snapshots.items():
        if identifier not in target_ids and digest(
            api.call("/api/v1/flows/" + identifier)
        ) != digest(flow):
            raise RuntimeError("An unrelated flow changed; inspect browser drafts")
    print("Applied. Backup:", backup)
    print("Native A2A retained; model/API credentials preserved. OpenAI/workflow calls: 0.")


async def export_sources() -> None:
    from uuid import NAMESPACE_URL, uuid5

    flows = {
        role: json.loads((ROOT / "flows/ver2" / (role + ".json")).read_text(encoding="utf-8"))
        for role in ENDPOINTS
    }
    identifiers = {
        role: str(uuid5(NAMESPACE_URL, endpoint)) for role, endpoint in ENDPOINTS.items()
    }
    # Preserve the existing export's child selections; exports contain no local credentials.
    slugs = {"hoi_du_lieu": "database", "hoi_du_doan": "prediction", "hoi_tri_thuc": "knowledge"}
    for item in flows["central"]["data"]["nodes"]:
        if item["data"]["type"] == "A2AAgent":
            fields = item["data"]["node"]["template"]
            role = slugs[fields["tools_metadata"]["value"][0]["name"]]
            picked = fields["agent_name_selected"].get("options_metadata", [])
            if len(picked) == 1:
                identifiers[role] = str(picked[0]["id"])
    targets = {role: {**flow, "id": identifiers[role]} for role, flow in flows.items()}
    prepared = {}
    for role, flow in flows.items():
        updated = deepcopy(flow)
        updated["data"] = await coordinated_graph(flow, role, targets)
        if role != "central":
            updated["description"] = SERVICE_DESCRIPTIONS[role]
            updated["a2a_card_overrides"]["description"] = SERVICE_DESCRIPTIONS[role]
        prepared[role] = updated
    for role, flow in prepared.items():
        write_export(role, flow)
        print("Exported", role, len(flow["data"]["nodes"]), "nodes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--exports-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(export_sources() if args.exports_only else upgrade(args.origin, args.apply))
