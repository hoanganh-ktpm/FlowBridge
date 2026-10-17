# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Prepare/clean an isolated RAG job using real identities from a local Keycloak profile."""

import argparse
import asyncio
import json
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa

from flowbridge.db import create_engine, create_session_factory
from flowbridge.settings import Settings
from flowbridge.supplier_assistant.repository import SupplierRepository, table

ROOT = Path(__file__).resolve().parents[2]


async def run(state: Path, cleanup: bool) -> None:
    identity_fixture = runpy.run_path(str(Path(__file__).with_name("identity_fixture.py")))["run"]
    runtime = json.loads((state / "runtime.json").read_text("utf-8"))
    settings = Settings(_env_file=ROOT / ".env", **runtime, ml_enabled=False)
    if not settings.oidc_issuer.startswith("https://localhost:"):
        raise RuntimeError("RAG fixture is restricted to an isolated local identity profile")
    path = state / "rag-fixture.json"
    engine = create_engine(settings, use_null_pool=True)
    sessions = create_session_factory(engine)
    try:
        if cleanup:
            if path.exists():
                fixture = json.loads(path.read_text("utf-8"))
                async with sessions() as session, session.begin():
                    tasks = table("analysis_tasks")
                    await session.execute(
                        sa.delete(tasks).where(
                            tasks.c.task_id == UUID(fixture["task_id"]),
                            tasks.c.request_id == UUID(fixture["request_id"]),
                            tasks.c.input_snapshot["fixture_kind"].astext
                            == "flowbridge-rag-langflow",
                        )
                    )
                path.unlink()
            if (state / "fixture.json").exists():
                await identity_fixture(state, True)
            print("Local RAG fixture cleaned; supplier documents and seed data retained.")
            return
        if path.exists():
            fixture = json.loads(path.read_text("utf-8"))
            async with sessions() as session:
                await SupplierRepository(session).get("analysis_tasks", UUID(fixture["task_id"]))
            print("Reusing the existing isolated RAG fixture.")
            return
        if not (state / "fixture.json").exists():
            await identity_fixture(state, False)
        identity = json.loads((state / "fixture.json").read_text("utf-8"))
        uid = uuid4()
        request_id, task_id = uuid4(), uuid4()
        timestamp = datetime.now(UTC)
        async with sessions() as session, session.begin():
            repo = SupplierRepository(session)
            owner_id = UUID(identity["users"]["employee"])
            await repo.insert(
                "purchase_requests",
                {
                    "request_id": request_id,
                    "request_code": "RAG-TEST-" + uid.hex,
                    "department_id": UUID(identity["department_id"]),
                    "requested_by": owner_id,
                    "source": "api",
                    "status": "analysing",
                    "input_revision": 1,
                    "submitted_at": timestamp,
                    "notes": "Isolated Langflow RAG test; no ML or procurement execution.",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
            await repo.insert(
                "purchase_request_items",
                {
                    "request_item_id": uuid4(),
                    "request_id": request_id,
                    "line_number": 1,
                    "product_id": UUID(identity["product_id"]),
                    "quantity": 1,
                    "requested_delivery_date": timestamp.date() + timedelta(days=30),
                    "currency": "VND",
                    "notes": "RAG test fixture only",
                },
            )
            await repo.insert(
                "analysis_tasks",
                {
                    "task_id": task_id,
                    "request_id": request_id,
                    "task_type": "initial_analysis",
                    "input_revision": 1,
                    "feature_asof": timestamp,
                    "input_snapshot": {"fixture_kind": "flowbridge-rag-langflow"},
                    "idempotency_key": "rag-test-" + uid.hex,
                    "status": "running",
                    "started_at": timestamp,
                    "created_at": timestamp,
                },
            )
        fixture = {"request_id": str(request_id), "task_id": str(task_id)}
        path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        print("Prepared an isolated RAG task after document publication; ML is not invoked.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-dir", required=True, type=Path)
    parser.add_argument("--cleanup", action="store_true")
    arguments = parser.parse_args()
    try:
        asyncio.run(run(arguments.identity_dir.resolve(), arguments.cleanup))
    except Exception as error:
        print(
            f"RAG fixture failed ({type(error).__name__}); inspect the local profile before retry"
        )
        raise SystemExit(1) from None
