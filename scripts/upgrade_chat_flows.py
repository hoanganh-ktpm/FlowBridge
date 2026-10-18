# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Reuse the repository's graph migrations through Langflow's authenticated metadata API.
import asyncio
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def upgrade() -> None:
    from add_ver2_protocol_nodes import insert_protocol_nodes
    from setup_ver2 import ENDPOINTS, digest
    from upgrade_ver2_coordination import coordinated_graph
    from upgrade_ver2_data import central_graph, data_graph

    settings = json.loads(Path("/langflow-config/runtime.json").read_text("utf-8"))
    import os

    port = os.environ.get("FLOWBRIDGE_CHAT_PORT", "7861")
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        headers={"x-api-key": settings["LANGFLOW_API_KEY"]},
        timeout=120,
        trust_env=False,
    ) as http:

        async def call(path: str, body: dict | None = None) -> dict | list:
            response = await (http.get(path) if body is None else http.patch(path, json=body))
            if response.status_code != 200:
                raise RuntimeError(f"Metadata API HTTP {response.status_code}; body suppressed")
            return response.json()

        listed = await call("/api/v1/flows/?get_all=true&remove_example_flows=true")
        listed = listed["items"] if isinstance(listed, dict) else listed
        targets = {}
        for role, endpoint in ENDPOINTS.items():
            found = [f for f in listed if f.get("endpoint_name") == endpoint]
            if len(found) != 1:
                raise ValueError("Expected four unambiguous ver2 endpoints")
            targets[role] = await call("/api/v1/flows/" + found[0]["id"])
        if len({f["folder_id"] for f in targets.values()}) != 1:
            raise ValueError("Ver2 flows must share a project")
        plans = {}
        for role, original in targets.items():
            flow = deepcopy(original)
            if role == "database":
                flow["data"] = data_graph(flow["data"])
            elif role == "central":
                flow["data"] = central_graph(flow["data"])
            flow["data"] = await coordinated_graph(flow, role, targets)
            if not any(
                n["id"].startswith("A2AProtocolAdapter-") or n["id"].startswith("A2AReplyEnvelope-")
                for n in flow["data"]["nodes"]
            ):
                flow["data"] = insert_protocol_nodes(flow, role)
            plans[role] = flow["data"]
        changed = [role for role in targets if plans[role] != targets[role]["data"]]
        if not changed:
            print("PASS: ver2 data/coordination/protocol metadata already current")
            return
        backup = Path("/var/lib/langflow/flowbridge-backups") / datetime.now(UTC).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        backup.mkdir(parents=True)
        for role, flow in targets.items():
            (backup / (role + ".json")).write_text(json.dumps(flow), "utf-8")
        applied = []
        try:
            for role in changed:
                original = targets[role]
                path = "/api/v1/flows/" + original["id"]
                if digest(await call(path)) != digest(original):
                    raise RuntimeError("Concurrent flow edit; refusing stale overwrite")
                applied.append(role)
                saved = await call(path, {"data": plans[role]})
                if saved["data"] != plans[role]:
                    raise RuntimeError("Saved flow differs from planned graph")
        except Exception:
            for role in reversed(applied):
                await call("/api/v1/flows/" + targets[role]["id"], {"data": targets[role]["data"]})
            raise
        print(
            "PASS: upgraded ver2 data/coordination/protocol; model and credential presets retained"
        )


if __name__ == "__main__":
    try:
        asyncio.run(upgrade())
    except Exception as exc:
        print(
            f"Flow metadata upgrade failed ({type(exc).__name__}); sensitive detail suppressed",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
