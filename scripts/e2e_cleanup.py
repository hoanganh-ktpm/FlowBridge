"""Cleanup only resources recorded in an approved existing-Supabase run ledger."""

import argparse
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from tests.e2e.test_automated_onboarding import call, wait_job

from app.core.db import models
from scripts.e2e_environment import ROOT, business_snapshot, engine_for, tokens
from scripts.e2e_runtime import NativeRuntime


def cleanup(directory):
    runtime = NativeRuntime(directory)
    values = dotenv_values(ROOT / ".env")
    runtime.start("api")
    try:
        with httpx.Client(
            base_url=runtime.base_url,
            timeout=35,
            headers={"Authorization": "Bearer " + tokens()["it"]},
        ) as client:
            runtime.start("worker")
            if runtime.resources.get("grant_id"):
                grant = call(client, "GET", "/access-grants/" + runtime.resources["grant_id"])
                if grant["status"] not in {"REVOKED", "REVOKING", "EXPIRED"}:
                    wait_job(
                        client,
                        call(
                            client,
                            "POST",
                            "/access-grants/" + grant["id"] + "/revoke",
                            202,
                            json={
                                "expected_desired_revision": grant["desired_revision"],
                                "reason": "Scoped E2E cleanup",
                            },
                        ),
                    )
                elif grant["status"] == "REVOKING":
                    wait_job(client, runtime.resources["revoke_job"])
            for imported in runtime.resources.get("imports", {}).values():
                for identifier in imported["data_product_ids"]:
                    product = call(client, "GET", "/data-products/" + identifier)
                    if product["status"] not in {"DRAFT", "DISABLED"}:
                        wait_job(
                            client,
                            call(
                                client,
                                "POST",
                                "/data-products/" + identifier + "/disable",
                                202,
                                json={
                                    "expected_revision": product["revision"],
                                    "reason": "Scoped E2E cleanup",
                                },
                            ),
                        )
                application = runtime.application(imported["application_id"])
                if application["status"] != "DISABLED":
                    call(
                        client,
                        "PATCH",
                        "/applications/" + application["id"],
                        json={"expected_revision": application["revision"], "status": "DISABLED"},
                    )
        runtime.evidence(
            "control_cleanup", "Owned grants revoked, published products/apps disabled"
        )
    finally:
        runtime.close()
    warehouse = engine_for(make_url(values["DXOS_WAREHOUSE_URL"]))
    try:
        if runtime.resources["created_fixture"]:
            with warehouse.begin() as connection:
                removed = connection.execute(
                    text(
                        "DELETE FROM public.products WHERE id=:id AND org_id=:org_id AND sku=:sku"
                    ),
                    runtime.resources["fixture"],
                ).rowcount
                assert removed in {0, 1}
            runtime.resources["created_fixture"] = False
            runtime.save()
    finally:
        warehouse.dispose()
    verified = {}
    for name in ("warehouse", "sales", "logistics"):
        snapshot = business_snapshot(make_url(values["DXOS_" + name.upper() + "_URL"]))
        assert snapshot == runtime.resources["baseline"][name], name + " baseline changed"
        verified[name] = "PASS: all public table counts and row fingerprints unchanged"
    runtime.evidence("database_safety", verified)
    core = engine_for(make_url(values["DXOS_CORE_URL"]))
    try:
        with core.begin() as connection:
            for identifier in runtime.resources["membership_ids"].values():
                connection.execute(
                    models.department_memberships.update()
                    .where(models.department_memberships.c.id == identifier)
                    .values(status="REVOKED")
                )
            for identifier in runtime.resources["principal_ids"].values():
                connection.execute(
                    models.principals.update()
                    .where(models.principals.c.id == identifier)
                    .values(status="DISABLED")
                )
            for department in runtime.resources["departments"].values():
                connection.execute(
                    models.departments.update()
                    .where(models.departments.c.id == department["id"])
                    .values(status="DISABLED")
                )
            connection.execute(
                models.audit_events.insert().values(
                    id=str(uuid4()),
                    occurred_at=text("now()"),
                    actor_kind="system",
                    action="e2e.cleanup",
                    resource_type="test_run",
                    resource_ref=runtime.resources["run_id"],
                    outcome="SUCCESS",
                    request_id=runtime.resources["run_id"],
                )
            )
    finally:
        core.dispose()
    runtime.resources["cleaned"] = True
    runtime.save()
    print("Scoped cleanup complete; business baselines verified, Core audit retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--allow-supabase-test-writes", action="store_true", required=True)
    cleanup(parser.parse_args().directory)
