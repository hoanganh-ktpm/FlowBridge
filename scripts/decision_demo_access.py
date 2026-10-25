# SPDX-License-Identifier: GPL-3.0-or-later
"""Publish local demo capabilities and request grants through actual Core commands."""

import json
import ssl
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import httpx
import sqlalchemy as sa
from dotenv import dotenv_values

from scripts.business_actions import definitions
from scripts.decision_demo_data import DIRECTORY, ROOT
from scripts.decision_demo_runtime import IDENTITY_RUNTIME, ISSUER, local_configuration, state

CORE = "http://127.0.0.1:18000"


def service_token(role):
    private = json.loads((DIRECTORY / "credentials.json").read_text())
    identity = private["clients"][role]
    ca = ssl.create_default_context(cafile=str(IDENTITY_RUNTIME / "tls/server.crt"))
    with httpx.Client(verify=ca, timeout=20, follow_redirects=False) as http:
        response = http.post(
            ISSUER + "/protocol/openid-connect/token",
            data={"grant_type": "client_credentials"},
            auth=(identity["client_id"], identity["secret"]),
        )
        if response.status_code != 200:
            raise RuntimeError("Demo service identity could not authenticate")
        return response.json()["access_token"]


def setup():
    from app.core.db import models
    from scripts.data_workspace import migrate
    from scripts.database.engine import make_engine
    from scripts.database.products import PRODUCTS

    local_configuration()
    ledger = state()
    for key, value in dotenv_values(DIRECTORY / "workspace.env").items():
        import os

        os.environ[key] = value
    migrate()
    file = DIRECTORY / "access-resources.json"
    access = (
        json.loads(file.read_text())
        if file.exists()
        else {
            "org_id": ledger["org_id"],
            "expires_at": (datetime.now(UTC) + timedelta(days=2))
            .isoformat()
            .replace("+00:00", "Z"),
            "grants": {},
            "actions": [],
            "deployment": None,
        }
    )
    if access["org_id"] != ledger["org_id"]:
        raise ValueError("Access ledger belongs to another organization")

    def record():
        file.write_text(json.dumps(access, indent=2), encoding="utf-8")

    record()
    engine = make_engine("core", hide_parameters=True)
    try:
        with engine.connect() as conn:
            products = (
                conn.execute(
                    sa.select(models.data_products.c.id, models.data_products.c.name).where(
                        models.data_products.c.status == "ACTIVE"
                    )
                )
                .mappings()
                .all()
            )
    finally:
        engine.dispose()
    expected = {domain + "." + key for domain, keys in PRODUCTS.items() for key in keys}
    selected = [p for p in products if p["name"] in expected]
    if len(selected) != len(expected) or len({p["name"] for p in selected}) != len(expected):
        raise ValueError(
            "Publish the reviewed local Data Products first; no metadata bypass"
        )
    with httpx.Client(
        base_url=CORE,
        timeout=40,
        headers={"Authorization": "Bearer " + service_token("setup")},
        follow_redirects=False,
    ) as http:

        def call(method, path, body=None, expected=200, key=None):
            response = http.request(
                method,
                path,
                json=body,
                headers={
                    "Idempotency-Key": key
                    or str(uuid5(UUID(ledger["org_id"]), path + json.dumps(body, sort_keys=True)))
                },
            )
            if response.status_code != expected:
                raise RuntimeError(
                    f"Demo setup {method} {path}: HTTP {response.status_code}; "
                    "inspect private runtime log"
                )
            return response.json()

        def wait(job):
            for _ in range(160):
                result = call("GET", "/api/v1/core/publication-jobs/" + job["job_id"])
                if result["status"] == "SUCCEEDED":
                    return
                if result["status"] in {"FAILED", "CANCELLED"}:
                    raise RuntimeError("Demo grant publication failed; inspect Core job through UI")
                time.sleep(0.5)
            raise RuntimeError("Demo grant publication timeout")

        for product in selected:
            key = str(product["id"])
            metadata = call("GET", "/api/v1/core/data-products/" + key)
            contract = call("GET", "/api/v1/core/data-contracts/" + metadata["active_contract_id"])
            columns = [f["column"] for f in contract["contract_json"]["fields"]]
            scope = {"action": "SELECT", "columns": sorted(columns), "row_scope": {"kind": "ALL"}}
            if key not in access["grants"]:
                request = call(
                    "POST",
                    "/api/v1/core/access-requests",
                    {
                        "data_product_id": key,
                        "contract_version_id": contract["id"],
                        "consumer_department_id": ledger["department_id"],
                        "consumer_principal_id": None,
                        "requested_scope_json": scope,
                        "reason": "Owned local Decision Workspace demo (48h)",
                    },
                    201,
                )
                approved = call(
                    "POST",
                    "/api/v1/core/access-requests/" + request["id"] + "/approve",
                    {
                        "expected_revision": request["revision"],
                        "scope_json": scope,
                        "expires_at": access["expires_at"],
                    },
                    202,
                )
                access["grants"][key] = {
                    "grant_id": approved["grant"]["id"],
                    "job": approved["job"],
                    "contract_version_id": contract["id"],
                    "name": product["name"],
                }
                record()
            grant = access["grants"][key]
            wait(grant["job"])
            current = call("GET", "/api/v1/core/access-grants/" + grant["grant_id"])
            if current["status"] != "ACTIVE":
                raise ValueError("An owned demo grant expired/revoked; do not silently recreate it")
        for definition in definitions()["actions"]:
            if definition["action_id"] in access["actions"]:
                continue
            call("POST", "/api/v1/workflows/actions", definition, 201)
            access["actions"].append(definition["action_id"])
            record()
        from scripts.database.fixtures.business import canonical_hash

        xml = (ROOT / "workflows/supply_handover_exception.bpmn20.xml").read_text(encoding="utf-8")
        hashed = canonical_hash({"bpmn": xml})
        if not access["deployment"] or access["deployment"]["hash"] != hashed:
            result = call(
                "POST",
                "/api/v1/workflows/definitions",
                {"name": "decision-demo-reviewed", "bpmn_xml": xml},
                201,
            )
            access["deployment"] = {"hash": hashed, "id": result["deployment"]["id"]}
            record()
        access["configured"] = True
        record()
    print(
        f"Local demo: {len(expected)} read grants, 23 native actions "
        "and BPMN deployed through Core. "
        "Human MFA acceptance is pending."
    )


if __name__ == "__main__":
    setup()
