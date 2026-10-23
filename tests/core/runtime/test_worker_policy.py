from datetime import timedelta
from uuid import UUID

import pytest

from app.core.db.ports import StoredResource

from .fakes import ISSUER, SALES, WAREHOUSE, MemoryStore, granted, published, read


async def queue_source(harness):
    response = await harness.request(
        "POST",
        "/applications",
        json={"department_id": WAREHOUSE, "app_key": "warehouse", "name": "Warehouse"},
    )
    application = response.json()
    await harness.request(
        "PATCH",
        "/applications/" + application["id"],
        json={"expected_revision": 1, "status": "ACTIVE"},
    )
    response = await harness.request(
        "POST",
        "/data-sources",
        json={
            "application_id": application["id"],
            "source_key": "warehouse.pg",
            "db_type": "POSTGRESQL",
            "host": "warehouse.example.test",
            "port": 5432,
            "database_name": "warehouse_db",
            "db_user": "reader",
            "secret_ref": "warehouse-reader",
        },
    )
    assert response.status_code == 201
    source = response.json()
    response = await harness.request(
        "POST", f"/data-sources/{source['id']}/connect", json={"expected_revision": 1}
    )
    assert response.status_code == 202
    return source, response.json()["job_id"]


@pytest.mark.asyncio
async def test_worker_retry_inspects_ambiguous_source_commit_instead_of_recreating(harness):
    source, job_id = await queue_source(harness)
    harness.engine.source_timeout_after_commit = True
    assert await harness.worker.run_once("worker-one")
    assert harness.record("job", job_id)["status"] == "RETRY"
    assert harness.record("source", source["id"])["status"] == "VALIDATING"
    assert not await harness.worker.run_once("worker-two")
    harness.store.clock += timedelta(seconds=3)
    assert await harness.worker.run_once("worker-two")
    assert harness.record("job", job_id)["status"] == "SUCCEEDED"
    assert harness.record("source", source["id"])["status"] == "CONNECTED"
    assert harness.engine.mutations.count("pg_add_source") == 1
    response = await harness.request("GET", f"/publication-jobs/{job_id}")
    assert response.status_code == 200 and response.json()["status"] == "SUCCEEDED"
    assert (
        not {"requested_by", "locked_by", "lease_expires_at", "idempotency_key"}
        & response.json().keys()
    )


@pytest.mark.asyncio
async def test_worker_terminal_failure_does_not_claim_connected(harness):
    source, job_id = await queue_source(harness)
    harness.record("job", job_id)["max_attempts"] = 1
    harness.engine.source_timeout_after_commit = True
    assert await harness.worker.run_once("worker-one")
    assert harness.record("job", job_id)["status"] == "FAILED"
    assert harness.record("source", source["id"])["status"] == "FAILED"
    response = await harness.request(
        "POST", f"/data-sources/{source['id']}/connect", json={"expected_revision": 2}
    )
    assert response.status_code == 202
    assert await harness.worker.run_once("worker-one")
    assert harness.record("source", source["id"])["status"] == "CONNECTED"


@pytest.mark.asyncio
async def test_permission_retry_after_engine_commit_keeps_grant_pending_until_verified(harness):
    _, _, product, contract, grant = await granted(harness)
    harness.engine.timeout_after = "pg_create_select_permission"
    assert await harness.worker.run_once("worker-one")
    assert harness.record("grant", grant["id"])["status"] == "PENDING_APPLY"
    assert (await read(harness, product, contract)).status_code == 403
    harness.store.clock += timedelta(seconds=3)
    assert await harness.worker.run_once("worker-one")
    assert harness.record("grant", grant["id"])["status"] == "ACTIVE"
    assert harness.engine.mutations.count("pg_create_select_permission") == 1


@pytest.mark.asyncio
async def test_worker_rechecks_it_authority_after_acceptance(harness):
    _, job_id = await queue_source(harness)
    harness.store.principals["admin"]["platform_role"] = "NONE"
    assert await harness.worker.run_once("worker-one")
    assert harness.record("job", job_id)["status"] == "FAILED"
    assert not harness.engine.mutations


@pytest.mark.asyncio
async def test_public_views_do_not_leak_internal_adapter_fields(harness):
    _, source, _, _, grant = await granted(harness)
    harness.record("source", source["id"])["secret_ref"] = "SYNTHETIC_PRIVATE"
    harness.record("grant", grant["id"])["enforcement_role"] = "SYNTHETIC_PRIVATE"
    for path in [f"/data-sources/{source['id']}", f"/access-grants/{grant['id']}"]:
        response = await harness.request("GET", path)
        assert response.status_code == 200 and "SYNTHETIC_PRIVATE" not in response.text


@pytest.mark.asyncio
async def test_catalog_hides_inactive_resources_and_admin_policy_precedes_lookup(harness):
    _, _, product, contract = await published(harness)
    harness.record("product", product["id"])["status"] = "DISABLED"
    for path in [f"/data-products/{product['id']}", f"/data-contracts/{contract['id']}"]:
        assert (await harness.request("GET", path, actor="consumer")).status_code == 404
        assert (await harness.request("GET", path)).status_code == 200
    response = await harness.request("GET", f"/publication-jobs/{UUID(int=999)}", actor="consumer")
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["kind", "issuer", "client", "disabled_app", "disabled_department", "none"]
)
async def test_service_and_human_bindings_are_checked_against_trusted_principals(harness, change):
    if change == "issuer":
        harness.store.bindings["admin"]["issuer"] = "https://other.example.test"
        assert (await harness.request("GET", "/me")).status_code == 403
        return
    if change == "kind":
        harness.store.bindings["admin"]["kind"] = "service"
        assert (await harness.request("GET", "/me")).status_code == 403
        return
    harness.dependencies.identity_settings.oidc_service_clients = ["service-client"]
    harness.store.principals["service"] = MemoryStore.principal(str(UUID(int=4)), "NONE", [])
    harness.store.bindings["service"] = {
        "issuer": ISSUER,
        "subject": "service",
        "kind": "service",
        "service_client_id": "service-client",
    }
    if change == "client":
        harness.store.bindings["service"]["service_client_id"] = "another-client"
    elif change in {"disabled_app", "disabled_department"}:
        application_id = str(UUID(int=5))
        harness.store.principals["service"]["application_id"] = application_id
        harness.store.data["application"][application_id] = StoredResource(
            {
                "id": application_id,
                "department_id": SALES,
                "status": "DISABLED" if change == "disabled_app" else "ACTIVE",
            },
            {},
        )
        if change == "disabled_department":
            harness.record("department", SALES)["status"] = "DISABLED"
    response = await harness.request("GET", "/me", actor="service")
    assert response.status_code == (200 if change == "none" else 403), response.text
    if change == "none":
        response = await harness.request(
            "POST",
            "/applications",
            actor="service",
            json={"department_id": WAREHOUSE, "app_key": "warehouse", "name": "Warehouse"},
        )
        assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": 1},
        {"expected_revision": 1, "status": None},
        {"expected_revision": 1, "name": None},
    ],
)
async def test_patch_matches_contract_non_null_optional_fields(harness, body):
    response = await harness.request("PATCH", f"/applications/{UUID(int=6)}", json=body)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_idempotency_uses_canonical_uuid_path_and_correlation_accepts_contract_alphabet(
    harness,
):
    application, _, _, _ = await published(harness)
    path = "/applications/" + application["id"]
    body = {"expected_revision": 2, "name": "Renamed"}
    first = await harness.request(
        "PATCH",
        path,
        json=body,
        key="canonical-path-key",
        headers={"X-Correlation-Id": "demo.trace:42"},
    )
    replay = await harness.request(
        "PATCH",
        path.upper().replace("/APPLICATIONS/", "/applications/"),
        json=body,
        key="canonical-path-key",
    )
    assert first.status_code == replay.status_code == 200 and first.json() == replay.json()
    assert first.headers["X-Correlation-Id"] == "demo.trace:42"
