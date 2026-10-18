# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Metadata only: no graph execution, model validation or OpenAI requests.
import argparse
import hashlib
import json
from copy import deepcopy

from setup_ver2 import ENDPOINTS, ROOT, LangflowAPI, digest

ORIGIN = "http://127.0.0.1:8001"
STATE = ROOT / "data/local-test-connection.json"


def patch_nodes(data: dict, credential: str, *, restore: dict | None = None) -> dict:
    saved = {}
    seen = set()
    for node in data["nodes"]:
        fields = node["data"]["node"]["template"]
        if not {"api_origin", "access_token"} <= fields.keys():
            continue
        identifier = node["id"]
        seen.add(identifier)
        if restore is not None:
            if identifier not in restore:
                raise ValueError("Tool nodes changed since connection; inspect before restoring")
            if (
                fields["api_origin"]["value"] != ORIGIN
                or fields["access_token"]["value"] != credential
            ):
                raise ValueError("Tool connection was edited; refusing to overwrite user changes")
            for name, before in restore[identifier].items():
                fields[name] = deepcopy(before)
        else:
            saved[identifier] = {
                name: deepcopy(fields[name]) for name in ["api_origin", "access_token"]
            }
            fields["api_origin"]["value"] = ORIGIN
            fields["access_token"]["value"] = credential
            fields["access_token"]["load_from_db"] = True
    if restore is not None and seen != restore.keys():
        raise ValueError("Tool nodes changed since connection; inspect before restoring")
    return saved


def connect(origin: str, *, disconnect: bool = False) -> None:
    profile = json.loads((ROOT / "data/local-test.json").read_text(encoding="utf-8"))
    if profile.get("version") != 1 or profile.get("origin") != ORIGIN:
        raise ValueError("Prepare the local test backend first")
    fingerprint = hashlib.sha256(profile["token"].encode()).hexdigest()[:12]
    credential = "FLOWBRIDGE_LOCAL_TEST_TOKEN_" + fingerprint
    api = LangflowAPI(origin)
    listed = api.call("/api/v1/flows/?get_all=true&remove_example_flows=true")
    if isinstance(listed, dict):
        listed = listed["items"]
    snapshots = {f["id"]: api.call("/api/v1/flows/" + f["id"]) for f in listed}
    endpoints = {ENDPOINTS["database"], ENDPOINTS["prediction"]}
    targets = [f for f in snapshots.values() if f.get("endpoint_name") in endpoints]
    if len(targets) != 2 or len({f.get("folder_id") for f in targets}) != 1:
        raise ValueError("Expected exactly two ver2 service flows in the same project")
    before_state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None
    if before_state and before_state["credential"] != credential:
        raise ValueError("Local profile differs from saved connection; restore previous profile")
    if disconnect and (not before_state or not before_state["active"]):
        print("Local test is already disconnected.")
        return
    plans, originals = [], {}
    for flow in targets:
        data = deepcopy(flow["data"])
        original = patch_nodes(
            data,
            credential,
            restore=before_state["flows"][flow["id"]] if disconnect else None,
        )
        expected = {3, 5} if flow["endpoint_name"] == ENDPOINTS["database"] else {4}
        if not disconnect and len(original) not in expected:
            raise ValueError("Unexpected ver2 API tool count")
        originals[flow["id"]] = original
        plans.append((flow, data))
    if not disconnect:
        variables = api.call("/api/v1/variables/")
        matches = [v for v in variables if v["name"] == credential]
        if matches and (len(matches) != 1 or matches[0]["type"] != "Credential"):
            raise ValueError("Local test credential name is already used with a different type")
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
        if not before_state or not before_state["active"]:
            before_state = {"credential": credential, "flows": originals, "active": True}
            STATE.write_text(
                json.dumps(before_state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
    for flow, data in plans:
        saved = api.call("/api/v1/flows/" + flow["id"], {"data": data}, "PATCH")
        if saved["data"] != data:
            raise RuntimeError("Langflow did not preserve the requested tool configuration")
    protected = {key: value for key, value in snapshots.items() if value not in targets}
    for identifier, before in protected.items():
        if digest(api.call("/api/v1/flows/" + identifier)) != digest(before):
            raise RuntimeError("Another workflow changed; inspect browser drafts")
    before_state["active"] = not disconnect
    STATE.write_text(
        json.dumps(before_state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("Ver2 local test " + ("disconnected." if disconnect else "connected: " + ORIGIN))
    print("Other workflows unchanged:", len(protected), "; OpenAI requests: 0.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Connect only ver2 data/ML tools to local test")
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    parser.add_argument("--disconnect", action="store_true")
    args = parser.parse_args()
    connect(args.origin, disconnect=args.disconnect)
