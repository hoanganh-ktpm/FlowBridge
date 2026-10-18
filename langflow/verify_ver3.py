# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

# Safe live smoke: only an exact greeting with a guaranteed zero-model code path.
import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from lfx.graph import Graph  # noqa: E402
from setup_ver2 import LangflowAPI, digest  # noqa: E402
from setup_ver3 import writable  # noqa: E402


def verify(origin: str, *, smoke: bool = False) -> None:
    manifest = json.loads((ROOT / "data/ver3-install.json").read_text(encoding="utf-8"))
    originals = json.loads(
        (Path(manifest["backup"]) / "ver2-originals.json").read_text(encoding="utf-8")
    )
    clones = json.loads(
        (Path(manifest["backup"]) / "ver3-exact-clone.json").read_text(encoding="utf-8")
    )
    api = LangflowAPI(origin)
    proof = {"source_unchanged": {}, "flows": {}, "openai_requests": 0}
    identifiers = {value["id"] for value in manifest["flows"].values()}
    for service, info in manifest["flows"].items():
        source = originals[service]
        assert (
            digest(api.call("/api/v1/flows/" + source["id"])) == manifest["source_hashes"][service]
        )
        proof["source_unchanged"][service] = True
        flow = api.call("/api/v1/flows/" + info["id"])
        assert flow["folder_id"] == manifest["project_id"]
        assert flow["a2a_enabled"] is (service != "central")
        graph = Graph.from_payload(flow["data"])
        assert len(graph.vertices) == len(flow["data"]["nodes"])
        for original in source["data"]["nodes"]:
            if "api_origin" not in original["data"]["node"]["template"]:
                continue
            copied = next(n for n in flow["data"]["nodes"] if n["id"] == original["id"])
            assert original == copied
        for item in flow["data"]["nodes"]:
            fields = item["data"]["node"]["template"]
            if "agent_name_selected" in fields:
                target = fields["agent_name_selected"]["options_metadata"][0]["id"]
                assert target in identifiers and target != flow["id"]
        assert len(clones[service]["data"]["nodes"]) == len(source["data"]["nodes"])
        assert len(clones[service]["data"]["edges"]) == len(source["data"]["edges"])
        proof["flows"][service] = {
            "nodes": len(graph.vertices),
            "edges": len(flow["data"]["edges"]),
            "hash": digest(writable(flow)),
        }
    if smoke:
        # No non-greeting input is accepted here, and this script cannot run paid tests.
        result = api.call(
            "/api/v1/run/session/" + manifest["flows"]["central"]["id"],
            {
                "input_value": "Bạn là ai?",
                "input_type": "chat",
                "output_type": "chat",
                "session_id": "ver3-zero-model-smoke-" + uuid4().hex,
            },
            "POST",
        )
        (ROOT / "data/ver3-greeting-smoke.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        messages = [
            value
            for run in result.get("outputs", [])
            for output in run.get("outputs", [])
            for value in output.get("results", {}).values()
            if isinstance(value, dict) and "text" in value
        ]
        assert messages and any("FlowBridge" in value["text"] for value in messages)
        # Langflow ChatOutput preserves the renderer's properties.
        traces = [m.get("session_metadata", {}).get("flowbridge_ver3", {}) for m in messages]
        assert any(t.get("model_calls") == 0 and t.get("service_calls") == 0 for t in traces)
        proof["greeting_smoke"] = {"model_calls": 0, "service_calls": 0, "passed": True}
    (ROOT / "data/ver3-verification.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(proof, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Verify ver3 metadata and optional zero-model greeting"
    )
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    parser.add_argument("--greeting-smoke", action="store_true")
    args = parser.parse_args()
    verify(args.origin, smoke=args.greeting_smoke)
