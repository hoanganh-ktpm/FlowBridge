# SPDX-License-Identifier: GPL-3.0-or-later
"""Install checked-in ver2 exports unchanged; refuse to replace an existing edited graph."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from setup_ver2 import KEY_VARIABLE, PROJECT, ROOT, LangflowAPI, digest, local_values


def local_api(origin: str) -> LangflowAPI:
    from urllib.parse import urlparse

    address = urlparse(origin)
    if address.scheme != "http" or address.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("LOCAL_LANGFLOW_REQUIRED")
    return LangflowAPI(origin)


def ensure_runtime_key(api: LangflowAPI) -> None:
    # A run key must belong to the private flow owner, not the older RAG user.
    key_path = ROOT / "data/langflow-client.json"
    current_key = json.loads(key_path.read_text("utf-8")) if key_path.exists() else None
    if current_key is None or datetime.fromisoformat(current_key["expires_at"]) <= datetime.now(
        UTC
    ):
        current_key = api.call(
            "/api/v1/api_key/",
            {
                "name": "FlowBridge ver2 JSON local test",
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        key_path.parent.mkdir(exist_ok=True)
        key_path.write_text(json.dumps(current_key, indent=2) + "\n", encoding="utf-8")


def install(origin: str) -> str:
    api = local_api(origin)
    flows = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true")
    if isinstance(flows, dict):
        flows = flows["items"]
    snapshots = {f["id"]: api.call("/api/v1/flows/" + f["id"]) for f in flows}
    exports = {
        service: json.loads((ROOT / f"flows/ver2/{service}.json").read_text("utf-8"))
        for service in ("database", "prediction", "knowledge", "central")
    }
    # IDs in source A2A selectors must continue to refer to the same flow IDs.
    ids = {service: str(uuid5(NAMESPACE_URL, f["endpoint_name"])) for service, f in exports.items()}
    for service, flow in exports.items():
        found = [f for f in flows if f.get("endpoint_name") == flow["endpoint_name"]]
        if len(found) > 1:
            raise ValueError("DUPLICATE_ENDPOINT")
        if found and (
            found[0]["id"] != ids[service] or snapshots[found[0]["id"]]["data"] != flow["data"]
        ):
            raise ValueError("EXISTING_FLOW_DIFFERS_PRESERVE_USER_EDITS")
    projects = [p for p in api.call("/api/v1/projects/") if p["name"] == PROJECT]
    if len(projects) > 1:
        raise ValueError("DUPLICATE_VER2_PROJECT")
    project = projects[0] if projects else api.call("/api/v1/projects/", {"name": PROJECT})
    variables = api.call("/api/v1/variables/")
    matches = [v for v in variables if v["name"] == KEY_VARIABLE]
    if not matches:
        key = local_values().get("OPENAI_API_KEY")
        if not key:
            raise ValueError("CONFIGURE_LOCAL_LLM_KEY_FIRST")
        api.call(
            "/api/v1/variables/",
            {
                "name": KEY_VARIABLE,
                "value": key,
                "type": "Credential",
                "default_fields": [],
            },
        )
    elif len(matches) != 1 or matches[0]["type"] != "Credential":
        raise ValueError("INVALID_EXISTING_LLM_CREDENTIAL")
    installed = {}
    for service, flow in exports.items():
        found = next((f for f in flows if f.get("endpoint_name") == flow["endpoint_name"]), None)
        if found:
            if found.get("folder_id") != project["id"]:
                raise ValueError("EXISTING_FLOW_BELONGS_TO_ANOTHER_PROJECT")
            result = snapshots[found["id"]]
        else:
            result = api.call(
                "/api/v1/flows/",
                {
                    **flow,
                    "id": ids[service],
                    "folder_id": project["id"],
                },
            )
        saved = api.call("/api/v1/flows/" + result["id"])
        if saved["id"] != ids[service] or saved["data"] != flow["data"]:
            raise RuntimeError("EXPORT_NOT_PRESERVED")
        installed[service] = {
            "id": saved["id"],
            "name": saved["name"],
            "url": origin + "/flow/" + saved["id"],
        }
    for identifier, before in snapshots.items():
        if digest(api.call("/api/v1/flows/" + identifier)) != digest(before):
            raise RuntimeError("PRE_EXISTING_FLOW_CHANGED")
    ensure_runtime_key(api)
    proof = {
        "project_id": project["id"],
        "flows": installed,
        "preserved_existing_flows": len(snapshots),
        "source_nodes_changed": 0,
        "openai_requests": 0,
        "business_api_requests": 0,
    }
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data/ver2-install.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return installed["central"]["url"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    parser.add_argument(
        "--refresh-key", action="store_true", help="Refresh only the run credential"
    )
    args = parser.parse_args()
    try:
        if args.refresh_key:
            ensure_runtime_key(local_api(args.origin))
            print("Local runtime key ready; graphs unchanged.")
        else:
            print("Ver2 ready: " + install(args.origin))
        print("Existing graphs and source exports unchanged; provider requests: 0.")
    except Exception as exc:
        print(f"Installation stopped ({type(exc).__name__}); private values hidden.")
        raise SystemExit(1) from None
