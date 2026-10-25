# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit local register publication; no hosted targets, fixture seeding or resets."""

import argparse
import json
import time
from uuid import UUID, uuid5

import httpx
import sqlalchemy as sa
from dotenv import dotenv_values

from scripts.decision_demo_access import CORE, service_token
from scripts.decision_demo_data import DIRECTORY
from scripts.decision_demo_runtime import local_configuration, state

REGISTER_KEYS = {
    "sales": {"order_register", "order_register_lines"},
    "warehouse": {"location_register", "product_register"},
}


def enable_all_warehouses():
    """Explicitly review current local warehouse orgs; never grants a new permission."""
    from scripts.database.engine import make_engine
    from scripts.integration_utils import write_env

    local_configuration()
    engine = make_engine("warehouse", hide_parameters=True)
    try:
        with engine.connect() as conn:
            organizations = [
                str(value)
                for value in conn.scalars(
                    sa.text("SELECT DISTINCT org_id FROM public.warehouses ORDER BY org_id")
                )
            ]
    finally:
        engine.dispose()
    if not organizations or len(organizations) > 20:
        raise ValueError("Review the bounded overview organization list")
    target = DIRECTORY / "workspace.env"
    current = dict(dotenv_values(target))
    if not current.get("FLOWBRIDGE_WORKSPACE_ORG_ID"):
        raise ValueError("Configure the owned Workspace first")
    write_env(target, {**current, "FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS": json.dumps(organizations)})
    (DIRECTORY / "overview-resources.json").write_text(
        json.dumps(
            {
                "reviewed_local_warehouse_organizations": organizations,
                "access": "Platform admin only; every source still requires current Core grants",
                "purpose": "Explicit user request to cover every warehouse "
                "already in local databases",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"Configured {len(organizations)} existing organization scopes "
        "for admin warehouse overview."
    )


def prepare():
    from scripts.database.cli import run_migrations
    from scripts.database.engine import make_engine
    from scripts.database.local import PRODUCT_VIEWS, RUNTIME
    from scripts.database.products import view_keys
    from scripts.integration_utils import write_env

    local_configuration()  # Validates all owner URLs before opening a connection.
    run_migrations(("sales", "warehouse"))
    for domain, keys in REGISTER_KEYS.items():
        engine = make_engine(domain, hide_parameters=True)
        try:
            with engine.begin() as conn:
                for key in sorted(keys):
                    conn.execute(sa.text(f"GRANT SELECT ON public.v_{key} TO {domain}_reader"))
        finally:
            engine.dispose()
    settings = {
        "FLOWBRIDGE_CORE_SOURCE_SCOPES": json.dumps(
            {
                f"{d}-reader": [{"schema": "public", "name": name} for name in views]
                for d, views in PRODUCT_VIEWS.items()
            }
        ),
        "FLOWBRIDGE_CORE_SOURCE_VIEW_KEYS": json.dumps(view_keys()),
    }
    for path in (RUNTIME / "host.env", DIRECTORY / "core.env"):
        current = dict(dotenv_values(path))
        if not current:
            raise ValueError("Prepare the existing owned local runtime first")
        write_env(path, {**current, **settings})
    print(
        "Four register views migrated; reader/config bindings updated. "
        "Restart owned Core/worker before publish."
    )


def publish():
    from app.core.db import models
    from scripts.database.engine import make_engine
    from scripts.database.products import draft_submissions
    from scripts.decision_demo_access import setup

    local_configuration()
    ledger = state()
    file = DIRECTORY / "register-resources.json"
    saved = (
        json.loads(file.read_text())
        if file.exists()
        else {"org_id": ledger["org_id"], "products": {}}
    )
    if saved["org_id"] != ledger["org_id"]:
        raise ValueError("Register ownership ledger mismatch")

    def record():
        file.write_text(json.dumps(saved, indent=2), encoding="utf-8")

    engine = make_engine("core", hide_parameters=True)
    try:
        with engine.connect() as conn:
            sources = {
                domain: str(
                    conn.scalar(
                        sa.select(models.data_products.c.data_source_id).where(
                            models.data_products.c.name == name,
                            models.data_products.c.status == "ACTIVE",
                        )
                    )
                )
                for domain, name in (
                    ("sales", "sales.open_orders"),
                    ("warehouse", "warehouse.planning_catalog"),
                )
            }
            for value in sources.values():
                UUID(value)
    finally:
        engine.dispose()
    proposals = draft_submissions()
    with httpx.Client(
        base_url=CORE,
        timeout=40,
        follow_redirects=False,
        headers={"Authorization": "Bearer " + service_token("setup")},
    ) as http:

        def call(method, path, body=None, expected=200):
            response = http.request(
                method,
                "/api/v1/core" + path,
                json=body,
                headers={
                    "Idempotency-Key": str(
                        uuid5(
                            UUID(ledger["org_id"]),
                            "register:" + path + json.dumps(body, sort_keys=True),
                        )
                    )
                },
            )
            if response.status_code != expected:
                # Error metadata only: no credential, customer body or bearer output.
                raise RuntimeError(f"Register setup {method} {path}: HTTP {response.status_code}")
            return response.json()

        def wait(job):
            for _ in range(120):
                current = call("GET", "/publication-jobs/" + job["job_id"])
                if current["status"] == "SUCCEEDED":
                    return
                if current["status"] in ("FAILED", "CANCELLED"):
                    raise RuntimeError("Register publication failed; inspect owned job")
                time.sleep(0.5)
            raise RuntimeError("Register publication timed out")

        for domain, keys in REGISTER_KEYS.items():
            # Discovery uses the updated deployment-owned view allowlist.
            call("GET", "/data-sources/" + sources[domain] + "/schema?schema_name=public")
            for key in sorted(keys):
                name = domain + "." + key
                if name not in saved["products"]:
                    product = call(
                        "POST",
                        "/data-products",
                        dict(
                            data_source_id=sources[domain],
                            product_key=name,
                            name=name,
                            schema_name="public",
                            object_name="v_" + key,
                            object_type="VIEW",
                            classification="INTERNAL",
                        ),
                        201,
                    )
                    saved["products"][name] = {"id": product["id"]}
                    record()
                item = saved["products"][name]
                product = call("GET", "/data-products/" + item["id"])
                if "contract_id" not in item:
                    proposal = next(
                        p for p in proposals[domain]["product_contracts"] if p["product_key"] == key
                    )
                    contract = call(
                        "POST",
                        "/data-products/" + item["id"] + "/contracts",
                        {
                            "version": proposal["version"],
                            "contract_json": proposal["contract_json"],
                        },
                        201,
                    )
                    item["contract_id"] = contract["id"]
                    record()
                contract = call("GET", "/data-contracts/" + item["contract_id"])
                if contract["status"] == "DRAFT":
                    call(
                        "POST",
                        "/data-contracts/" + contract["id"] + "/publish",
                        {"expected_revision": contract["revision"]},
                    )
                if product["status"] != "ACTIVE":
                    if "job" not in item:
                        item["job"] = call(
                            "POST",
                            "/data-products/" + item["id"] + "/publish",
                            {
                                "expected_revision": product["revision"],
                                "contract_version_id": contract["id"],
                            },
                            202,
                        )
                        record()
                    wait(item["job"])
                if call("GET", "/data-products/" + item["id"])["status"] != "ACTIVE":
                    raise RuntimeError("Register did not become active")
    setup()  # Existing grant ledger prevents silently recreating revoked grants.
    print("Registers published and current-org read access prepared through Core.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "publish", "enable-all-warehouses"))
    args = parser.parse_args()
    if args.operation == "enable-all-warehouses":
        enable_all_warehouses()
    else:
        prepare() if args.operation == "prepare" else publish()
