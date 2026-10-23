"""Explicit existing-database acceptance; native production composition, no mocks."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from dotenv import dotenv_values
from pydantic import SecretStr
from scripts.e2e_environment import (
    ROOT,
    RUNTIME,
    business_snapshot,
    engine_for,
    expired_token,
    tokens,
)
from scripts.e2e_runtime import NativeRuntime
from sqlalchemy import text
from sqlalchemy.engine import make_url
from tests.e2e.test_automated_onboarding import call, import_manifest, permission, wait_job

from app.core.data_products.credentials import HasuraJwtCredentials
from app.core.data_products.reading import TrustedHasuraContext
from app.core.hasura_provisioner.schemas import department_role


@pytest.fixture(scope="module")
def live():
    directory = os.environ.get("FLOWBRIDGE_E2E_RUN_DIR")
    if not directory:
        pytest.skip("Opt-in: prepare an approved run and set FLOWBRIDGE_E2E_RUN_DIR")
    runtime = NativeRuntime(directory)
    assert make_url(runtime.env["FLOWBRIDGE_CORE_DATABASE_URL"]).database == "dxos_core_db"
    runtime.state = json.loads((RUNTIME / "secrets.json").read_text())
    runtime.clients = {}
    runtime.start("api")
    runtime.http = httpx.Client(timeout=35)
    runtime.admin = {
        "x-hasura-admin-secret": runtime.state["hasura_secret"],
    }
    runtime.metadata = "http://127.0.0.1:18080/v1/metadata"
    runtime.graphql = "http://127.0.0.1:18080/v1/graphql"
    runtime.urls = dotenv_values(ROOT / ".env")

    def refresh():
        for client in runtime.clients.values():
            client.close()
        runtime.clients = {
            name: httpx.Client(
                base_url=runtime.base_url,
                timeout=35,
                headers={"Authorization": "Bearer " + token},
            )
            for name, token in tokens().items()
        }

    runtime.refresh = refresh
    try:
        refresh()
        yield runtime
    finally:
        runtime.close()
        runtime.http.close()
        for client in runtime.clients.values():
            client.close()


def metadata(live, operation="export_metadata", args=None):
    response = live.http.post(
        live.metadata,
        headers=live.admin,
        json={
            "type": operation,
            "version": 2 if operation == "export_metadata" else 1,
            "args": args or {},
        },
    )
    assert response.status_code == 200, "Hasura Metadata API failed"
    return response.json()["metadata"] if operation == "export_metadata" else response.json()


def receipt(live, name):
    return live.resources["imports"][name]


@pytest.mark.integration
def test_01_keycloak_native_core(live):
    it = live.clients["it"]
    assert call(it, "GET", "/me")["platform_role"] == "PLATFORM_ADMIN"
    assert call(live.clients["sales"], "GET", "/me")["platform_role"] == "NONE"
    for name in ("unregistered", "disabled"):
        call(live.clients[name], "GET", "/me", 403)
    submission = json.loads((live.directory / "warehouse.json").read_text())
    assert call(it, "POST", "/application-onboarding/validate", json=submission)["valid"]
    call(
        live.clients["sales"],
        "POST",
        "/application-onboarding/validate",
        403,
        json=submission,
        headers={"X-Role": "PLATFORM_ADMIN", "X-Hasura-Role": "admin"},
    )
    for headers in ({}, {"Authorization": "Bearer invalid", "X-Role": "PLATFORM_ADMIN"}):
        with httpx.Client(base_url=live.base_url, headers=headers) as client:
            call(client, "GET", "/me", 401)
    with httpx.Client(
        base_url=live.base_url, headers={"Authorization": "Bearer " + expired_token()}
    ) as client:
        call(client, "GET", "/me", 401)
    live.evidence(
        "keycloak", "PASS: real issuer/introspection, Core roles, invalid/expired token denial"
    )


@pytest.mark.integration
def test_02_hasura_real_jwt_and_source_reader(live):
    assert live.http.get("http://127.0.0.1:18080/healthz").status_code == 200
    metadata(live)
    for headers in (
        {},
        {"X-Hasura-Role": "admin"},
        {"Authorization": live.clients["sales"].headers["Authorization"]},
    ):
        body = live.http.post(live.graphql, headers=headers, json={"query": "{__typename}"}).json()
        assert body.get("errors") and not body.get("data")
    identifier = UUID(live.resources["principal_ids"]["sales"])
    context = TrustedHasuraContext(
        principal_id=identifier,
        department_id=UUID(live.resources["departments"]["sales"]["id"]),
        data_product_id=uuid4(),
        contract_version_id=uuid4(),
        grant_id=uuid4(),
        desired_revision=1,
        applied_revision=1,
        validated_at=datetime.now(UTC),
        expires_at=None,
    )
    for key, valid in ((live.state["signing_key"], True), ("wrong-key-" * 5, False)):
        credentials = HasuraJwtCredentials(SecretStr(key), "flowbridge-core", "flowbridge-data")
        token = asyncio.run(credentials.issue(context)).get_secret_value()
        body = live.http.post(
            live.graphql,
            headers={"Authorization": "Bearer " + token},
            json={"query": "{__typename}"},
        ).json()
        if valid:
            assert body.get("data") == {"__typename": "query_root"}
        else:
            assert body.get("errors") and not body.get("data")
    reader = engine_for(
        make_url(live.env["FB_E2E_WAREHOUSE_URL"]).set(drivername="postgresql+psycopg")
    )
    try:
        with reader.connect() as connection:
            assert connection.connection.driver_connection.pgconn.ssl_in_use
            assert (
                connection.scalar(
                    text("SELECT sku FROM products WHERE id=:id"), live.resources["fixture"]
                )
                == live.resources["fixture"]["sku"]
            )
            assert not connection.scalar(
                text("SELECT has_table_privilege(current_user,'public.products','INSERT')")
            )
            has_private_column = connection.scalar(
                text(
                    "SELECT has_column_privilege("
                    "current_user,'public.products','unit_cost','SELECT')"
                )
            )
            approved = json.loads((RUNTIME / "source-readers.json").read_text())["warehouse"].get(
                "table_select_approved", False
            )
            assert has_private_column == approved
    finally:
        reader.dispose()
    live.env["FLOWBRIDGE_CORE_VERIFIED_HASURA_AUTH_PROFILE"] = "true"
    live.restart("api")
    live.evidence("hasura", "PASS: actual Metadata API, TLS reader, signed JWT and negative auth")


@pytest.mark.e2e
def test_03_onboarding_to_revoke(live):
    if live.resources["evidence"].get("E08"):
        pytest.skip("E01-E08 already verified for this run; use a fresh prepared run to repeat")
    assert live.resources["evidence"].get("keycloak") and live.resources["evidence"].get("hasura")
    live.refresh()
    it = live.clients["it"]
    live.env["FLOWBRIDGE_CORE_VERIFIED_HASURA_AUTH_PROFILE"] = "true"
    if "imports" not in live.resources:
        live.resources["imports"] = {}
    call(it, "POST", "/application-onboarding/validate", 422, json={"manifest": {}})
    for name in ("warehouse", "sales"):
        if name not in live.resources["imports"]:
            submission = json.loads((live.directory / (name + ".json")).read_text())
            live.resources["imports"][name] = import_manifest(it, submission)
            live.save()
    imported = receipt(live, "warehouse")
    application_id = imported["application_id"]
    source_id = imported["data_source_ids"][0]
    product_id = imported["data_product_ids"][0]
    contract_id = imported["contract_version_ids"][0]
    if "E01" not in live.resources["evidence"]:
        assert live.application(application_id)["status"] == "DRAFT"
        assert call(it, "GET", "/data-products/" + product_id)["status"] == "DRAFT"
        core = engine_for(
            make_url(live.env["FLOWBRIDGE_CORE_DATABASE_URL"]).set(drivername="postgresql+psycopg")
        )
        try:
            with core.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM publication_jobs WHERE data_source_id=:source "
                            "OR data_product_id=:product"
                        ),
                        {"source": source_id, "product": product_id},
                    )
                    == 0
                )
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM access_grants WHERE data_product_id=:product"),
                        {"product": product_id},
                    )
                    == 0
                )
        finally:
            core.dispose()
        live.evidence("E01", "PASS: LIVE_VERIFIED, draft only, idempotent import replay")
    application = live.application(application_id)
    if application["status"] == "DRAFT":
        body = {"expected_revision": application["revision"], "status": "ACTIVE"}
        key = {"Idempotency-Key": uuid4().hex}
        activated = call(it, "PATCH", "/applications/" + application_id, json=body, headers=key)
        live.resources["activation"] = {"body": body, "headers": key, "response": activated}
        live.save()
    live.start("worker")
    source = call(it, "GET", "/data-sources/" + source_id)
    if source["status"] == "VALIDATING":
        wait_job(it, live.source_job(source_id))
    elif source["status"] != "CONNECTED":
        wait_job(
            it,
            call(
                it,
                "POST",
                "/data-sources/" + source_id + "/connect",
                202,
                json={"expected_revision": source["revision"]},
            ),
        )
    source = call(it, "GET", "/data-sources/" + source_id)
    assert source["status"] == "CONNECTED"
    discovered = call(
        it,
        "GET",
        "/data-sources/" + source_id + "/schema",
        params={"schema_name": "public", "object_names": "products"},
    )
    assert len(discovered["objects"]) == 1
    assert discovered["objects"][0]["primary_key"] == ["id"]
    source_key = source["hasura_source_name"]
    assert next(item for item in metadata(live)["sources"] if item["name"] == source_key)[
        "configuration"
    ]["connection_info"]["database_url"] == {"from_env": "FB_E2E_WAREHOUSE_URL"}
    live.evidence("E02", "PASS: real source connect job SUCCEEDED and metadata from_env readback")
    contract = call(it, "GET", "/data-contracts/" + contract_id)
    if contract["status"] == "DRAFT":
        call(
            it,
            "POST",
            "/data-contracts/" + contract_id + "/publish",
            json={"expected_revision": contract["revision"]},
        )
    product = call(it, "GET", "/data-products/" + product_id)
    if product["status"] != "ACTIVE":
        wait_job(
            it,
            call(
                it,
                "POST",
                "/data-products/" + product_id + "/publish",
                202,
                json={"expected_revision": product["revision"], "contract_version_id": contract_id},
            ),
        )
    assert call(it, "GET", "/data-products/" + product_id)["status"] == "ACTIVE"
    live.evidence("E03", "PASS: actual tracked table, publication verified before ACTIVE")
    path = "/data-products/" + product_id + "/rows"
    params = {"contract_version_id": contract_id, "columns": "id,sku,name", "limit": 100}
    scope = {"action": "SELECT", "columns": ["id", "sku", "name"], "row_scope": {"kind": "ALL"}}
    department = live.resources["departments"]["sales"]["id"]
    role = department_role(UUID(department))
    live.stop("worker")
    if "grant_id" not in live.resources:
        requested = call(
            it,
            "POST",
            "/access-requests",
            201,
            json={
                "data_product_id": product_id,
                "contract_version_id": contract_id,
                "consumer_department_id": department,
                "consumer_principal_id": None,
                "requested_scope_json": scope,
                "reason": "Scoped existing Supabase E2E",
            },
        )
        approved = call(
            it,
            "POST",
            "/access-requests/" + requested["id"] + "/approve",
            202,
            json={
                "expected_revision": requested["revision"],
                "scope_json": scope,
                "expires_at": None,
            },
        )
        live.resources["grant_id"] = approved["grant"]["id"]
        live.resources["grant_job"] = approved["job"]
        live.save()
        assert approved["grant"]["status"] == "PENDING_APPLY"
        call(live.clients["sales"], "GET", path, 403, params=params)
    live.start("worker")
    wait_job(it, live.resources["grant_job"])
    grant = call(it, "GET", "/access-grants/" + live.resources["grant_id"])
    assert grant["status"] == "ACTIVE" and grant["applied_revision"] == grant["desired_revision"]
    assert set(permission(metadata(live), source_key, role, "products")["columns"]) == set(
        scope["columns"]
    )
    live.evidence(
        "E04", "PASS: PENDING_APPLY denied, applied revision matches, permission readback"
    )

    def fixture_row():
        rows = call(live.clients["sales"], "GET", path, params=params)["rows"]
        return next(item for item in rows if item["id"] == live.resources["fixture"]["id"])

    assert fixture_row()["sku"] == live.resources["fixture"]["sku"]
    call(live.clients["sales"], "GET", path, 403, params={**params, "columns": "id,sku,uom"})
    call(live.clients["sales"], "GET", path, 422, params={**params, "columns": "id,sku,unit_cost"})
    live.evidence("E05", "PASS: real governed rows, excluded grant/contract columns rejected")
    for name in ("outsider", "it"):
        call(live.clients[name], "GET", path, 403, params=params)
    call(
        live.clients["outsider"],
        "GET",
        path,
        403,
        params=params,
        headers={"X-Hasura-Role": role, "X-Role": "PLATFORM_ADMIN"},
    )
    core = engine_for(
        make_url(live.env["FLOWBRIDGE_CORE_DATABASE_URL"]).set(drivername="postgresql+psycopg")
    )
    try:
        with core.begin() as connection:
            connection.execute(
                text("UPDATE department_memberships SET status='REVOKED' WHERE id=:id"),
                {"id": live.resources["membership_ids"]["sales"]},
            )
        try:
            call(live.clients["sales"], "GET", path, 403, params=params)
        finally:
            with core.begin() as connection:
                connection.execute(
                    text("UPDATE department_memberships SET status='ACTIVE' WHERE id=:id"),
                    {"id": live.resources["membership_ids"]["sales"]},
                )
    finally:
        core.dispose()
    live.evidence("E06", "PASS: outsider, invalid membership, IT without grant, forged role denied")
    owner = engine_for(make_url(live.urls["DXOS_WAREHOUSE_URL"]))
    try:
        with owner.begin() as connection:
            assert (
                connection.execute(
                    text(
                        "UPDATE products SET name='DXOS E2E source 35' "
                        "WHERE id=:id AND org_id=:org_id AND sku=:sku"
                    ),
                    live.resources["fixture"],
                ).rowcount
                == 1
            )
        assert fixture_row()["name"] == "DXOS E2E source 35"
        assert (
            business_snapshot(make_url(live.urls["DXOS_SALES_URL"]))
            == live.resources["baseline"]["sales"]
        )
    finally:
        owner.dispose()
    live.evidence(
        "E07",
        "PASS: catalog fixture name 20 -> 35 read live; Sales unchanged (not numeric inventory)",
    )
    live.restart("api")
    replay = live.resources["activation"]
    assert (
        call(
            it,
            "PATCH",
            "/applications/" + application_id,
            json=replay["body"],
            headers=replay["headers"],
        )
        == replay["response"]
    )
    assert fixture_row()["name"] == "DXOS E2E source 35"
    live.stop("worker")
    revoked = call(
        it,
        "POST",
        "/access-grants/" + grant["id"] + "/revoke",
        202,
        json={"expected_desired_revision": grant["desired_revision"], "reason": "E2E complete"},
    )
    live.resources["revoke_job"] = revoked
    live.save()
    call(live.clients["sales"], "GET", path, 403, params=params)
    assert permission(metadata(live), source_key, role, "products") is not None
    live.start("worker")
    wait_job(it, revoked)
    assert permission(metadata(live), source_key, role, "products") is None
    call(live.clients["sales"], "GET", path, 403, params=params)
    live.evidence("E08", "PASS: immediate Core denial, real permission removed and verified")
    live.evidence(
        "E09_partial",
        "PASS: idempotency, native API/worker restarts, pending grant denied, invalid Manifest",
    )


def job_status(live, job, terminal):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        response = live.clients["it"].get("/api/v1/core/publication-jobs/" + job["job_id"])
        if response.status_code == 409:
            continue
        assert response.status_code == 200
        snapshot = response.json()
        if snapshot["status"] in terminal:
            return snapshot
        time.sleep(0.5)
    pytest.fail("Job did not reach the expected state")


@pytest.mark.e2e
def test_04_real_fault_retry_and_publication_failure(live):
    assert live.resources["evidence"].get("E08"), "Complete sharing/revoke before fault tests"
    live.stop("worker")
    live.stop("worker2")
    live.refresh()
    it = live.clients["it"]
    imported = receipt(live, "sales")
    application = live.application(imported["application_id"])
    if application["status"] == "DRAFT":
        call(
            it,
            "PATCH",
            "/applications/" + application["id"],
            json={"expected_revision": application["revision"], "status": "ACTIVE"},
        )
    source_id = imported["data_source_ids"][0]
    source = call(it, "GET", "/data-sources/" + source_id)
    if "retry_observed" not in live.resources["evidence"]:
        paused = False
        try:
            live.hasura_fault(True)
            paused = True
            job = live.resources.get("retry_job") if source["status"] == "VALIDATING" else None
            if job is None:
                job = call(
                    it,
                    "POST",
                    "/data-sources/" + source_id + "/connect",
                    202,
                    json={"expected_revision": source["revision"]},
                )
            live.resources["retry_job"] = job
            live.save()
            live.start("worker")
            live.start("worker2")
            snapshot = job_status(live, job, {"RETRY", "FAILED"})
            assert snapshot["status"] == "RETRY"
            assert all(live.processes[name].poll() is None for name in ("worker", "worker2"))
            live.evidence(
                "retry_observed",
                {"attempt": snapshot["attempt_count"], "code": snapshot["last_error_code"]},
            )
        finally:
            live.stop("worker")
            live.stop("worker2")
            if paused:
                live.hasura_fault(False)
    live.start("worker")
    live.start("worker2")
    wait_job(it, live.resources["retry_job"])
    snapshot = job_status(live, live.resources["retry_job"], {"SUCCEEDED"})
    assert snapshot["attempt_count"] >= 2
    source = call(it, "GET", "/data-sources/" + source_id)
    source_key = source["hasura_source_name"]
    assert len([item for item in metadata(live)["sources"] if item["name"] == source_key]) == 1
    live.stop("worker2")
    contract_id = imported["contract_version_ids"][0]
    product_id = imported["data_product_ids"][0]
    contract = call(it, "GET", "/data-contracts/" + contract_id)
    if contract["status"] == "DRAFT":
        call(
            it,
            "POST",
            "/data-contracts/" + contract_id + "/publish",
            json={"expected_revision": contract["revision"]},
        )
    table = {"schema": "public", "name": "customers"}
    source_metadata = next(item for item in metadata(live)["sources"] if item["name"] == source_key)
    if not source_metadata["tables"]:
        metadata(live, "pg_track_table", {"source": source_key, "table": table})
    try:
        metadata(
            live,
            "pg_set_table_customization",
            {
                "source": source_key,
                "table": table,
                "configuration": {"column_config": {"id": {"custom_name": "legacy_id"}}},
            },
        )
        product = call(it, "GET", "/data-products/" + product_id)
        job = call(
            it,
            "POST",
            "/data-products/" + product_id + "/publish",
            202,
            json={"expected_revision": product["revision"], "contract_version_id": contract_id},
        )
        live.resources["failed_publication"] = job
        live.save()
        snapshot = job_status(live, job, {"FAILED", "SUCCEEDED"})
        assert snapshot["status"] == "FAILED"
        assert call(it, "GET", "/data-products/" + product_id)["status"] == "ERROR"
        live.evidence(
            "publication_failure",
            {"status": "FAILED", "product": "ERROR", "code": snapshot["last_error_code"]},
        )
    finally:
        metadata(
            live,
            "pg_set_table_customization",
            {
                "source": source_key,
                "table": table,
                "configuration": {"column_config": {}},
            },
        )
    product = call(it, "GET", "/data-products/" + product_id)
    wait_job(
        it,
        call(
            it,
            "POST",
            "/data-products/" + product_id + "/publish",
            202,
            json={"expected_revision": product["revision"], "contract_version_id": contract_id},
        ),
    )
    assert call(it, "GET", "/data-products/" + product_id)["status"] == "ACTIVE"
    secrets_to_check = [
        live.state[name] for name in ("hasura_secret", "signing_key", "user_password", "api_secret")
    ]
    secrets_to_check.extend(
        value for name, value in live.env.items() if name.startswith("FB_E2E_") and value
    )
    secrets_to_check.extend(
        make_url(value).password
        for name, value in live.env.items()
        if name.startswith("FB_E2E_") and value
    )
    secrets_to_check.extend(
        client.headers["Authorization"].removeprefix("Bearer ") for client in live.clients.values()
    )
    responses = json.dumps(
        [
            call(it, "GET", "/data-sources/" + source_id),
            snapshot,
            call(it, "GET", "/data-products/" + product_id),
        ]
    )
    assert not any(secret in responses for secret in secrets_to_check)
    live.stop("worker")
    for path in live.directory.glob("*.log"):
        content = path.read_text(encoding="utf-8", errors="replace")
        assert not any(secret in content for secret in secrets_to_check), (
            "Secret detected in private runtime log"
        )
    live.evidence(
        "E09",
        "PASS: real Hasura outage retry, two workers, failure not ACTIVE, "
        "restarts/idempotency, no secret in responses/runtime logs",
    )
