# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Call the actual Langflow flow; credentials stay in env or an ignored local profile."""

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / ".cache/rag/langflow-integration"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--origin", default="http://127.0.0.1:7860")
    parser.add_argument("--flow-id")
    parser.add_argument("--task-id")
    parser.add_argument("--supplier-id", default="3806e329-2ebe-5cfc-895d-1bd60894b348")
    parser.add_argument("--query", default="Đơn đặt hàng DEMO-PO-00212 giao trễ như thế nào?")
    parser.add_argument("--expected-text", default="26 ngày")
    args = parser.parse_args()
    try:
        metadata = json.loads((args.profile / "langflow-flow.json").read_text("utf-8"))
        key = os.environ.get("LANGFLOW_API_KEY")
        if not key:
            key = json.loads((args.profile / "langflow-client.json").read_text("utf-8"))["api_key"]
        task = args.task_id
        if not task:
            task = json.loads((args.profile / "identity/rag-fixture.json").read_text("utf-8"))[
                "task_id"
            ]
        task_id, supplier_id = str(UUID(task)), str(UUID(args.supplier_id))
        origin = httpx.URL(args.origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.host
            or origin.userinfo
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
            or (origin.scheme == "http" and origin.host not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError("Invalid Langflow origin")
        with httpx.Client(timeout=180, trust_env=False, follow_redirects=False) as client:
            response = client.post(
                str(origin).rstrip("/") + "/api/v1/run/" + (args.flow_id or metadata["flow_id"]),
                headers={"x-api-key": key},
                json={
                    "input_value": args.query,
                    "input_type": "chat",
                    "output_type": "chat",
                    "tweaks": {
                        metadata["nodes"]["FlowBridgeRetrieveKnowledge"]: {
                            "analysis_task_id": task_id,
                            "supplier_id": supplier_id,
                        }
                    },
                },
            )
        if response.status_code != 200:
            print(f"Langflow RAG failed: HTTP {response.status_code}; inspect local service logs.")
            return 1
        messages = [
            out["results"]["message"]["text"]
            for run in response.json()["outputs"]
            for out in run["outputs"]
            if "message" in out.get("results", {})
        ]
        if not any(args.expected_text in text and "core-document://" in text for text in messages):
            raise ValueError("Expected evidence and citation were not returned")
        print("PASS: Langflow returned the expected RAG passage and versioned citation.")
        return 0
    except Exception as error:
        print(f"Langflow RAG check failed ({type(error).__name__}); no credentials are printed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
