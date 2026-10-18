# SPDX-License-Identifier: GPL-3.0-or-later
"""Run one real ver2 conversation and save the validated frontend JSON locally."""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT.parent / "backend")]


def run_wire(flow_id: str, question: str | None) -> dict:
    key = os.environ.get("LANGFLOW_API_KEY")
    if not key:
        candidates = [ROOT / "data/langflow-client.json"]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            raise ValueError("LOCAL_LANGFLOW_API_KEY_REQUIRED")
        key = json.loads(path.read_text("utf-8"))["api_key"]
    response = httpx.post(
        "http://127.0.0.1:7860/api/v1/run/" + flow_id,
        headers={"x-api-key": key},
        json={
            "input_value": question,
            "input_type": "chat",
            "output_type": "any",
            "output_component": "FrontendResponse-ver2-central",
            "session_id": "frontend-json-" + uuid4().hex,
        },
        timeout=180,
        trust_env=False,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    from flowbridge_agent.frontend_response_api import extract_frontend_response

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query")
    args = parser.parse_args()
    question = args.query or input("Nhap cau hoi (Enter de dung): ").strip()
    if not question:
        return
    if len(question) > 2000:
        raise ValueError("QUESTION_TOO_LONG")
    print("Running ver2 with the configured LLM key; provider usage may be billed.", flush=True)
    installed = json.loads((ROOT / "data/ver2-install.json").read_text("utf-8"))
    wire = run_wire(installed["flows"]["central"]["id"], question)
    payload = extract_frontend_response(wire)
    target = ROOT / "data/frontend-response-last.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("\nSaved: " + str(target))


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as exc:
        print(f"Langflow JSON run failed: HTTP {exc.response.status_code}; private details hidden.")
        raise SystemExit(1) from None
    except Exception as exc:
        print(f"JSON run failed ({type(exc).__name__}); inspect local service logs.")
        raise SystemExit(1) from None
