from copy import deepcopy
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from app.core.applications.onboarding import MissingIntegration
from app.core.data_products.schemas import DataContractDocument
from app.core.db.commands import timestamp
from app.core.hasura_provisioner.worker import LeaseLost, WorkerGrants
from app.main import create_app

from .fakes import ADMIN, SALES, WAREHOUSE, contract_document, granted, published, read


@pytest.mark.asyncio
async def test_http_vertical_slice_publish_grant_fresh_read_and_immediate_revoke(harness):
    _, source, product, contract, grant = await granted(harness)
    assert grant["status"] == "PENDING_APPLY" and grant["applied_revision"] == 0
    assert (await read(harness, product, contract)).status_code == 403
    assert await harness.worker.run_once("worker-one")
    assert harness.record("grant", grant["id"])["status"] == "ACTIVE"
    response = await read(harness, product, contract)
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [{"sku": "SKU-1", "available": 5}]
    harness.engine.rows[0]["available_quantity"] = 9
    assert (await read(harness, product, contract)).json()["rows"][0]["available"] == 9
    assert len(harness.engine.data_requests) == 2
    source_response = await harness.request("GET", f"/data-sources/{source['id']}")
    assert not {"host", "secret_ref", "db_user", "database_name"} & source_response.json().keys()
    response = await harness.request(
        "POST",
        f"/access-grants/{grant['id']}/revoke",
        json={"expected_desired_revision": 1, "reason": "End access"},
    )
    assert response.status_code == 202
    assert harness.record("grant", grant["id"])["status"] == "REVOKING"
    requests_before = len(harness.engine.data_requests)
    assert (await read(harness, product, contract)).status_code == 403
    assert len(harness.engine.data_requests) == requests_before
    assert await harness.worker.run_once("worker-one")
    assert harness.record("grant", grant["id"])["status"] == "REVOKED"
    assert not harness.engine.metadata["sources"][0]["tables"][0]["select_permissions"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,status", [("consumer", 403), ("owner", 403), ("unknown", 403), ("invalid", 401)]
)
async def test_only_trusted_it_can_register_and_headers_do_not_elevate(harness, actor, status):
    response = await harness.request(
        "POST",
        "/applications",
        actor=actor,
        headers={"X-Role": "PLATFORM_ADMIN", "X-Principal-Id": ADMIN, "X-Department-Id": WAREHOUSE},
        json={"department_id": WAREHOUSE, "app_key": "warehouse", "name": "Warehouse"},
    )
    assert response.status_code == status, response.text
    assert not harness.store.data["application"]


@pytest.mark.asyncio
async def test_missing_token_duplicate_keys_nonfinite_json_and_role_payload(harness):
    response = await harness.request(
        "GET", f"/data-products/{uuid4()}", headers={"Authorization": ""}
    )
    assert response.status_code == 401 and response.headers["WWW-Authenticate"] == "Bearer"
    for content in [b'{"name":', b'{"name":"first","name":"second"}', b'{"name":NaN}']:
        response = await harness.request(
            "POST", "/applications", content=content, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400
    response = await harness.request(
        "POST",
        "/applications",
        json={
            "department_id": WAREHOUSE,
            "app_key": "warehouse",
            "name": "Warehouse",
            "platform_role": "PLATFORM_ADMIN",
        },
    )
    assert response.status_code == 422 and "PLATFORM_ADMIN" not in response.text


@pytest.mark.asyncio
async def test_idempotency_replay_revision_conflict_and_current_policy(harness):
    body = {"department_id": WAREHOUSE, "app_key": "warehouse", "name": "Warehouse"}
    first = await harness.request("POST", "/applications", json=body, key="same-command-key")
    replay = await harness.request(
        "POST",
        "/applications",
        json=body,
        key="same-command-key",
        headers={"X-Correlation-Id": "new-request"},
    )
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json() and replay.headers["X-Correlation-Id"] == "new-request"
    assert len(harness.store.audits) == 1
    conflict = await harness.request(
        "POST", "/applications", json={**body, "name": "Changed"}, key="same-command-key"
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    application_id = first.json()["id"]
    command = {"expected_revision": 1, "status": "ACTIVE"}
    first = await harness.request(
        "PATCH", "/applications/" + application_id, json=command, key="activate-app-key"
    )
    assert first.status_code == 200
    replay = await harness.request(
        "PATCH", "/applications/" + application_id, json=command, key="activate-app-key"
    )
    assert replay.json() == first.json()
    assert (
        await harness.request("PATCH", "/applications/" + application_id, json=command)
    ).status_code == 409
    harness.store.principals["admin"]["platform_role"] = "NONE"
    assert (
        await harness.request("POST", "/applications", json=body, key="same-command-key")
    ).status_code == 403


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_resource_and_receipt(harness):
    harness.store.audit_failure = True
    response = await harness.request(
        "POST",
        "/applications",
        json={"department_id": WAREHOUSE, "app_key": "warehouse", "name": "Warehouse"},
    )
    assert response.status_code == 500 and "SYNTHETIC_PRIVATE_DATABASE_ERROR" not in response.text
    assert not harness.store.data["application"] and not harness.store.receipts


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PENDING_APPLY", "REVOKING", "REVOKED", "EXPIRED", "ERROR"])
async def test_non_active_grants_never_reach_hasura(harness, state):
    _, _, product, contract, grant = await granted(harness)
    harness.record("grant", grant["id"])["status"] = state
    assert (await read(harness, product, contract)).status_code == 403
    assert not harness.engine.data_requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "expired",
        "membership_expired",
        "membership_revoked",
        "department_disabled",
        "owner_department_disabled",
        "application_disabled",
        "source_disabled",
        "product_disabled",
        "principal_disabled",
        "revision_unapplied",
    ],
)
async def test_current_policy_denies_before_reconciliation(harness, change):
    application, source, product, contract, grant = await granted(harness)
    assert await harness.worker.run_once("worker-one")
    if change == "expired":
        harness.record("grant", grant["id"])["expires_at"] = timestamp(
            harness.store.clock - timedelta(seconds=1)
        )
    elif change == "membership_expired":
        harness.store.principals["consumer"]["memberships"][0]["expires_at"] = timestamp(
            harness.store.clock - timedelta(seconds=1)
        )
    elif change == "membership_revoked":
        harness.store.principals["consumer"]["memberships"][0]["status"] = "REVOKED"
    elif change in {"department_disabled", "owner_department_disabled"}:
        harness.record("department", SALES if change == "department_disabled" else WAREHOUSE)[
            "status"
        ] = "DISABLED"
    elif change == "application_disabled":
        harness.record("application", application["id"])["status"] = "DISABLED"
    elif change == "source_disabled":
        harness.record("source", source["id"])["status"] = "DISABLED"
    elif change == "product_disabled":
        harness.record("product", product["id"])["status"] = "DISABLED"
    elif change == "principal_disabled":
        harness.store.principals["consumer"]["principal_status"] = "DISABLED"
    else:
        harness.record("grant", grant["id"])["desired_revision"] += 1
    response = await read(harness, product, contract)
    assert response.status_code == 403, response.text
    assert not harness.engine.data_requests


@pytest.mark.asyncio
async def test_it_has_no_implicit_read_and_unrelated_grants_are_not_unioned(harness):
    _, _, product, contract, grant = await granted(harness)
    assert await harness.worker.run_once("worker-one")
    assert (await read(harness, product, contract, actor="admin")).status_code == 403
    original = harness.store.data["grant"][grant["id"]]
    first, second = deepcopy(original), deepcopy(original)
    first.public["scope_json"]["columns"] = ["sku"]
    second.public["id"] = str(uuid4())
    second.public["scope_json"]["columns"] = ["available_quantity"]
    harness.store.data["grant"] = {first.public["id"]: first, second.public["id"]: second}
    assert (await read(harness, product, contract)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "columns", ["available", "sku,sku", "sku,secret", "sku,available_quantity", "sku,__schema"]
)
async def test_projection_requires_keys_and_pinned_public_names(harness, columns):
    _, _, product, contract, _ = await granted(harness)
    response = await read(
        harness,
        product,
        contract,
        params={"contract_version_id": contract["id"], "columns": columns},
    )
    assert response.status_code == 422 and not harness.engine.data_requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["partial", "negative", "null", "float_integer", "unsafe_integer", "timeout"]
)
async def test_invalid_upstream_never_leaks_partial_data(harness, failure):
    _, _, product, contract, _ = await granted(harness)
    assert await harness.worker.run_once("worker-one")
    if failure == "partial":
        harness.engine.partial_error = True
    elif failure == "timeout":
        harness.engine.timeout = True
    else:
        harness.engine.rows[0]["available_quantity"] = {
            "negative": -1,
            "null": None,
            "float_integer": 1.0,
            "unsafe_integer": 2**54,
        }[failure]
    response = await read(harness, product, contract)
    assert response.status_code == (504 if failure == "timeout" else 503), response.text
    assert "rows" not in response.json() and "SYNTHETIC_PRIVATE_ERROR" not in response.text


@pytest.mark.asyncio
async def test_limit_plus_one_and_alias_mapping(harness):
    _, _, product, contract, _ = await granted(harness)
    assert await harness.worker.run_once("worker-one")
    harness.engine.rows.append({"sku": "SKU-2", "available_quantity": 7})
    params = {"contract_version_id": contract["id"], "columns": "sku,available", "limit": 1}
    response = await read(harness, product, contract, params=params)
    assert response.status_code == 200 and response.json()["has_more"]
    assert len(response.json()["rows"]) == 1
    assert harness.engine.data_requests[-1]["variables"]["limit"] == 2
    assert "available_quantity:available" in harness.engine.data_requests[-1]["query"]
    harness.engine.rows[1]["available_quantity"] = -1
    assert (await read(harness, product, contract, params=params)).status_code == 503


@pytest.mark.asyncio
async def test_published_immutable_draft_editable_and_version_switch_fenced(harness):
    _, _, product, contract, _ = await granted(harness)
    response = await harness.request(
        "PATCH",
        f"/data-contracts/{contract['id']}",
        json={"expected_revision": 2, "contract_json": contract_document()},
    )
    assert response.status_code == 409
    response = await harness.request(
        "POST",
        f"/data-products/{product['id']}/contracts",
        json={"version": "1.1.0", "contract_json": contract_document()},
    )
    assert response.status_code == 201
    draft = response.json()
    updated = contract_document()
    updated["grain"] = "Revised grain description"
    response = await harness.request(
        "PATCH",
        f"/data-contracts/{draft['id']}",
        json={"expected_revision": 1, "contract_json": updated},
    )
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert (
        await harness.request(
            "POST", f"/data-contracts/{draft['id']}/publish", json={"expected_revision": 2}
        )
    ).status_code == 200
    response = await harness.request(
        "POST",
        f"/data-products/{product['id']}/publish",
        json={"expected_revision": 2, "contract_version_id": draft["id"]},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_discovery_is_bounded_and_redacted(harness):
    _, source, _, _ = await published(harness)
    response = await harness.request("GET", f"/data-sources/{source['id']}/schema")
    assert response.status_code == 200 and response.json()["objects"][0]["primary_key"] == ["sku"]
    response = await harness.request(
        "GET", f"/data-sources/{source['id']}/schema", params={"object_names": "secret_table"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_disable_denies_immediately_and_worker_removes_permissions(harness):
    _, _, product, contract, grant = await granted(harness)
    assert await harness.worker.run_once("worker-one")
    response = await harness.request(
        "POST",
        f"/data-products/{product['id']}/disable",
        json={"expected_revision": 2, "reason": "Withdraw publication"},
    )
    assert response.status_code == 202
    assert (await read(harness, product, contract)).status_code == 403
    assert await harness.worker.run_once("worker-one")
    assert harness.record("grant", grant["id"])["status"] == "REVOKED"


@pytest.mark.asyncio
async def test_old_attempt_cannot_renew_or_acknowledge_after_reclaim(harness):
    await granted(harness)
    old = await harness.worker.claim("old-worker")
    harness.store.clock += timedelta(seconds=31)
    new = await harness.worker.claim("new-worker")
    assert new.job_id == old.job_id and new.attempt == old.attempt + 1
    with pytest.raises(LeaseLost):
        await harness.worker.renew(old)
    with pytest.raises(LeaseLost):
        await WorkerGrants(harness.worker, old).mark_applied(
            UUID(next(iter(harness.store.data["grant"]))), 1
        )


@pytest.mark.asyncio
async def test_stale_apply_cannot_resurrect_revoked_grant(harness):
    _, _, product, contract, grant = await granted(harness)
    response = await harness.request(
        "POST",
        f"/access-grants/{grant['id']}/revoke",
        json={"expected_desired_revision": 1, "reason": "Cancel before apply"},
    )
    assert response.status_code == 202
    assert await harness.worker.run_once("worker-one")
    assert (await read(harness, product, contract)).status_code == 403
    assert await harness.worker.run_once("worker-one")
    apply_job = next(
        item.public
        for item in harness.store.data["job"].values()
        if item.public["job_type"] == "APPLY_GRANT"
    )
    assert apply_job["status"] == "FAILED" and apply_job["last_error_code"] == "STALE_REVISION"
    assert harness.record("grant", grant["id"])["status"] == "REVOKED"


@pytest.mark.asyncio
async def test_missing_production_adapters_fail_closed(harness):
    with pytest.raises(MissingIntegration):
        create_app()
    with pytest.raises(MissingIntegration):
        create_app(harness.dependencies)


@pytest.mark.parametrize(
    "edit", ["duplicate_alias", "nullable_key", "alias_collision", "contradictory_bounds"]
)
def test_canonical_contract_rejects_ambiguous_semantics(edit):
    from pydantic import ValidationError

    document = contract_document()
    if edit == "duplicate_alias":
        document["fields"][1]["name"] = "sku"
    elif edit == "nullable_key":
        document["fields"][0]["nullable"] = True
    elif edit == "alias_collision":
        document["fields"][0]["name"] = "available_quantity"
    else:
        document["fields"][1]["constraints"] = {"minimum": 10, "maximum": 0}
    with pytest.raises(ValidationError):
        DataContractDocument.model_validate(document)
