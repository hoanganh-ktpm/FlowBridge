# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in real Core/Hasura/SSO + Workspace PostgreSQL integration.

Creates only expiring demo grants through Core APIs; never writes business databases.
Reads the prior local publication ledger to locate existing products (not their schemas).
"""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from dotenv import dotenv_values
from scripts.integration_utils import ROOT, tokens

pytestmark = pytest.mark.e2e
PREFIX = "/api/v1/data-workspace"


def call(client, method, path, expected=200, **kw):
    response = client.request(method, path, **kw)
    assert response.status_code == expected, (path, response.status_code, response.text[:300])
    return response.json()


def wait_job(client, job):
    job = job.get("job", job)
    for _ in range(120):
        result = call(client, "GET", "/api/v1/core/publication-jobs/" + job["job_id"])
        if result["status"] == "SUCCEEDED":
            return
        assert result["status"] not in {"FAILED", "SUPERSEDED"}, result["status"]
        time.sleep(0.1)
    pytest.fail("Core publication worker timeout")


@pytest.fixture(scope="module")
def live_workspace():
    if os.environ.get("FLOWBRIDGE_WORKSPACE_E2E") != "1":
        pytest.skip("BLOCKED: opt in with FLOWBRIDGE_WORKSPACE_E2E=1 after local services start")
    auth = tokens()
    clients = {
        "it": httpx.Client(
            base_url="http://127.0.0.1:18000",
            timeout=60,
            headers={"Authorization": "Bearer " + auth["it"]},
        ),
        "sales": httpx.Client(
            base_url="http://127.0.0.1:18000",
            timeout=60,
            headers={"Authorization": "Bearer " + auth["sales"]},
        ),
        "workspace": httpx.Client(
            base_url="http://127.0.0.1:18090",
            timeout=120,
            headers={"Authorization": "Bearer " + auth["sales"]},
        ),
        "outsider": httpx.Client(
            base_url="http://127.0.0.1:18090",
            timeout=120,
            headers={"Authorization": "Bearer " + auth["outsider"]},
        ),
    }
    ledger_paths = sorted(
        (ROOT / ".tmp/docker-integration/runs").glob("local-postgres-*/resources.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    assert ledger_paths, "Existing six-product local publication ledger required"
    ledger = json.loads(ledger_paths[0].read_text())
    me = call(clients["sales"], "GET", "/api/v1/core/me")
    department = next(m["department_id"] for m in me["memberships"] if m["status"] == "ACTIVE")
    grants, products = [], {}
    try:
        for imported in ledger["imports"].values():
            for identifier in imported["data_product_ids"]:
                p = call(clients["it"], "GET", "/api/v1/core/data-products/" + identifier)
                c = call(
                    clients["it"], "GET", "/api/v1/core/data-contracts/" + p["active_contract_id"]
                )
                scope = {
                    "action": "SELECT",
                    "columns": [f["column"] for f in c["contract_json"]["fields"]],
                    "row_scope": {"kind": "ALL"},
                }
                body = {
                    "data_product_id": identifier,
                    "contract_version_id": c["id"],
                    "consumer_department_id": department,
                    "consumer_principal_id": None,
                    "requested_scope_json": scope,
                    "reason": "Data Workspace controlled E2E",
                }
                r = call(
                    clients["it"],
                    "POST",
                    "/api/v1/core/access-requests",
                    201,
                    json=body,
                    headers={"Idempotency-Key": str(uuid4())},
                )
                job = call(
                    clients["it"],
                    "POST",
                    "/api/v1/core/access-requests/" + r["id"] + "/approve",
                    202,
                    json={
                        "expected_revision": r["revision"],
                        "scope_json": scope,
                        "expires_at": (datetime.now(UTC) + timedelta(hours=1))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    },
                    headers={"Idempotency-Key": str(uuid4())},
                )
                grants.append(job["grant"])
                wait_job(clients["it"], job)
                products[p["name"]] = {
                    "data_product_id": identifier,
                    "contract_version_id": c["id"],
                }
        yield clients, products, grants
    finally:
        for grant in grants:
            current = call(clients["it"], "GET", "/api/v1/core/access-grants/" + grant["id"])
            if current["status"] in {"REVOKED", "EXPIRED"}:
                continue
            job = call(
                clients["it"],
                "POST",
                "/api/v1/core/access-grants/" + grant["id"] + "/revoke",
                202,
                json={
                    "expected_desired_revision": current["desired_revision"],
                    "reason": "Controlled Workspace E2E complete",
                },
                headers={"Idempotency-Key": str(uuid4())},
            )
            wait_job(clients["it"], job)
        for client in clients.values():
            client.close()


def test_real_products_forecast_simulation_persistence_and_revoke(live_workspace):
    clients, products, grants = live_workspace
    workspace = clients["workspace"]
    catalog = call(workspace, "GET", PREFIX + "/products")
    assert len(catalog["items"]) >= 6
    assert call(clients["outsider"], "GET", PREFIX + "/products")["items"] == []
    results = {}
    for name, entity_column, target_column, unit in [
        ("sales.confirmed_demand_daily", "sku", "confirmed_quantity", "EA"),
        ("logistics.accepted_shipments_daily", "carrier_code", "accepted_shipments", "dispatches"),
    ]:
        reference = products[name]
        profile = call(
            workspace,
            "POST",
            PREFIX + "/profile",
            json={**reference, "time_column": "day", "entity_column": entity_column},
        )
        assert profile["rows"] > 100 and len(profile["entities"]) > 1
        # These published GROUP BY daily flow views omit non-occurrence days.
        config = {
            **reference,
            "name": name,
            "time_column": "day",
            "entity_column": entity_column,
            "target_column": target_column,
            "unit": unit,
            "metric_type": "FLOW",
            "frequency": "D",
            "aggregation": "SUM",
            "missing_policy": "zero",
            "start": profile["start"],
            "end": profile["end"],
            "forecast_horizon": 14,
            "forecast_model": "chronos2",
            "forecast_start": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat(),
        }
        analytical = call(workspace, "POST", PREFIX + "/analytics", json=config)
        assert len(analytical["series"]) > 100
        forecast = call(workspace, "POST", PREFIX + "/forecasts", 201, json={"config": config})
        assert forecast["model"]["method"] == "chronos2"
        assert forecast["model"]["pretrained"] is True
        assert forecast["model"]["fine_tuning"] is False
        assert len(forecast["points"]) == len(profile["entities"]) * 14
        assert call(workspace, "GET", PREFIX + "/forecasts/" + forecast["id"]) == forecast
        results[name] = forecast
    stocks = call(
        workspace, "POST", PREFIX + "/stock-options", json=products["warehouse.stock_availability"]
    )["items"]
    stock = stocks[0]
    forecast = results["sales.confirmed_demand_daily"]
    assert stock["uom"] == forecast["config"]["unit"], "Declare actual published UOM in E2E recipe"
    first = datetime.fromisoformat(forecast["points"][0]["timestamp"]).date()
    body = {
        "forecast_run_id": forecast["id"],
        "sku": stock["sku"],
        "warehouse_id": stock["warehouse_id"],
        "stock": products["warehouse.stock_availability"],
        "inbound": products["warehouse.inbound_schedule"],
        "open_orders": products["sales.open_orders"],
        "commitments": products["warehouse.order_commitments"],
        "arrival_date": first.isoformat(),
    }
    baseline = call(workspace, "POST", PREFIX + "/scenarios", json=body)
    assert baseline["baseline"] == baseline["proposed"]
    body["snapshot_id"] = baseline["evidence"]["snapshot_id"]
    early = call(
        workspace,
        "POST",
        PREFIX + "/scenarios",
        json={
            **body,
            "additional_quantity": 50,
            "arrival_date": (first + timedelta(days=2)).isoformat(),
            "save": True,
        },
    )
    late = call(
        workspace,
        "POST",
        PREFIX + "/scenarios",
        json={
            **body,
            "additional_quantity": 50,
            "arrival_date": (first + timedelta(days=5)).isoformat(),
        },
    )
    assert (
        early["proposed"]["series"][2]["net_position"]
        == late["proposed"]["series"][2]["net_position"] + 50
    )
    assert early["proposed"]["series"][1] == early["baseline"]["series"][1]
    assert early["evidence"]["input_sha256"] == late["evidence"]["input_sha256"]
    assert call(workspace, "GET", PREFIX + "/scenarios/" + early["id"]) == early
    assert all(p["physical_inventory"] >= 0 for p in early["proposed"]["series"])
    assert early["proposed"]["metrics"]["known_orders_at_risk"] is not None
    # A fresh OS process must read the same PostgreSQL results with current grants.
    with (ROOT / ".tmp/data-workspace/restarted-process.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.runtime:create_app",
                "--factory",
                "--app-dir",
                str(ROOT / "services/data-workspace-api"),
                "--host",
                "127.0.0.1",
                "--port",
                "18091",
            ],
            cwd=ROOT,
            env={**os.environ, **dotenv_values(ROOT / ".tmp/data-workspace/runtime.env")},
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            for _ in range(60):
                assert process.poll() is None, "Fresh Workspace process failed"
                try:
                    if (
                        httpx.get("http://127.0.0.1:18091/openapi.json", timeout=1).status_code
                        == 200
                    ):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            with httpx.Client(
                base_url="http://127.0.0.1:18091", headers=workspace.headers, timeout=90
            ) as fresh:
                assert call(fresh, "GET", PREFIX + "/forecasts/" + forecast["id"]) == forecast
                assert call(fresh, "GET", PREFIX + "/scenarios/" + early["id"]) == early
        finally:
            process.terminate()
            process.wait(timeout=10)
    # New DB repository instance/process restart is verified separately by restart/read step.
    evidence = {
        "forecasts": [r["id"] for r in results.values()],
        "scenario": early["id"],
        "checks": [
            "real SSO",
            "Core/Hasura pagination",
            "two domain recipes",
            "PostgreSQL persisted results",
            "day 3/day 6 supply timing",
            "physical conservation",
            "outsider denied",
            "fresh OS process read",
            "known backlog and commitments",
        ],
    }
    Path(ROOT / ".tmp/data-workspace/live-evidence.json").write_text(json.dumps(evidence, indent=2))
    sales_grant = next(
        g for g in grants if g["data_product_id"] == forecast["config"]["data_product_id"]
    )
    current = call(clients["it"], "GET", "/api/v1/core/access-grants/" + sales_grant["id"])
    job = call(
        clients["it"],
        "POST",
        "/api/v1/core/access-grants/" + sales_grant["id"] + "/revoke",
        202,
        json={
            "expected_desired_revision": current["desired_revision"],
            "reason": "Verify stored-result revocation",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    wait_job(clients["it"], job)
    call(workspace, "GET", PREFIX + "/forecasts/" + forecast["id"], 403)
    call(workspace, "GET", PREFIX + "/scenarios/" + early["id"], 403)
    call(workspace, "POST", PREFIX + "/scenarios", 403, json=body)
