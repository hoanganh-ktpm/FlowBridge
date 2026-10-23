from copy import deepcopy
from datetime import timedelta

import pytest

from .fakes import contract_document, published


def submission():
    return {
        "manifest": {
            "manifest_version": "1.0",
            "application": {"key": "warehouse", "name": "Warehouse", "department_key": "warehouse"},
            "sources": [
                {
                    "key": "pg",
                    "kind": "postgres",
                    "host": "warehouse.example.test",
                    "database": "warehouse_db",
                    "username": "reader",
                    "ssl_mode": "verify-full",
                    "secret_ref": "warehouse-reader",
                }
            ],
            "data_products": [
                {
                    "key": "inventory",
                    "name": "Available stock",
                    "source_key": "pg",
                    "object": {"schema": "public", "name": "stock_available", "kind": "table"},
                    "fields": [
                        {"column": "sku", "expected_type": "text"},
                        {
                            "column": "available_quantity",
                            "alias": "available",
                            "expected_type": "integer",
                        },
                    ],
                    "contract_version": "1.0.0",
                    "requested_state": "PUBLISH_REQUESTED",
                }
            ],
        },
        "product_contracts": [
            {"product_key": "inventory", "version": "1.0.0", "contract_json": contract_document()}
        ],
    }


@pytest.mark.asyncio
async def test_static_validation_and_plan_are_read_only_and_redacted(harness):
    response = await harness.request("POST", "/application-onboarding/validate", json=submission())
    assert response.status_code == 200 and response.json()["valid"]
    response = await harness.request(
        "POST", "/application-onboarding/plans", json={"submission": submission()}
    )
    assert response.status_code == 200, response.text
    assert response.json()["evidence"] == "STATIC_UNKNOWN"
    assert not response.json()["ready_for_dispatch"]
    assert harness.discovery.calls == 0 and not harness.engine.mutations
    for secret in [
        "warehouse.example.test",
        "WAREHOUSE_URL",
        "warehouse-reader",
        "database_name",
        "column_config",
    ]:
        assert secret not in response.text
    assert not harness.store.data["application"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["alias", "description", "type", "version", "missing", "duplicate"]
)
async def test_contradictory_canonical_annotations_are_not_silently_dropped(harness, change):
    body = submission()
    proposal = body["product_contracts"][0]
    if change == "alias":
        proposal["contract_json"]["fields"][1]["name"] = "changed"
    elif change == "description":
        proposal["contract_json"]["fields"][0]["description"] = "Different"
    elif change == "type":
        proposal["contract_json"]["fields"][1]["physical_type"] = "TEXT"
    elif change == "version":
        proposal["version"] = "2.0.0"
    elif change == "missing":
        body["manifest"]["data_products"].append(
            {
                **deepcopy(body["manifest"]["data_products"][0]),
                "key": "other",
                "object": {"schema": "public", "name": "other", "kind": "table"},
            }
        )
    else:
        body["product_contracts"].append(deepcopy(proposal))
    response = await harness.request("POST", "/application-onboarding/validate", json=body)
    assert response.status_code == 200 and not response.json()["valid"], response.text
    assert not harness.discovery.calls and not harness.engine.mutations


@pytest.mark.asyncio
async def test_live_plan_imports_canonical_drafts_atomically_and_replays(harness):
    body = submission()
    response = await harness.request(
        "POST", "/application-onboarding/plans", json={"submission": body, "discovery_mode": "LIVE"}
    )
    assert response.status_code == 200 and response.json()["valid"], response.text
    plan = response.json()
    assert plan["evidence"] == "LIVE_VERIFIED" and not plan["ready_for_dispatch"]
    payload = {
        "submission": body,
        "plan_hash": plan["plan_hash"],
        "expected_application_revision": 0,
    }
    response = await harness.request(
        "POST", "/application-onboarding/imports", json=payload, key="atomic-import-key"
    )
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["result"] == "DRAFTS_SAVED"
    contract = harness.record("contract", receipt["contract_version_ids"][0])
    assert contract["contract_json"] == contract_document()
    assert contract["status"] == "DRAFT"
    assert (
        not harness.engine.mutations
        and not harness.store.data["job"]
        and not harness.store.data["grant"]
    )
    replay = await harness.request(
        "POST", "/application-onboarding/imports", json=payload, key="atomic-import-key"
    )
    assert replay.status_code == 201 and replay.json() == receipt
    assert len(harness.store.data["contract"]) == len(harness.store.data["source"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expires", "physical", "hash", "revision"])
async def test_import_recompiles_live_evidence_and_rejects_stale_plan(harness, change):
    body = submission()
    response = await harness.request(
        "POST", "/application-onboarding/plans", json={"submission": body, "discovery_mode": "LIVE"}
    )
    payload = {
        "submission": body,
        "plan_hash": response.json()["plan_hash"],
        "expected_application_revision": 0,
    }
    if change == "expires":
        harness.store.clock += timedelta(minutes=6)
    elif change == "physical":
        harness.discovery.nullable = True
    elif change == "hash":
        payload["plan_hash"] = "f" * 64
    else:
        payload["expected_application_revision"] = 2
    response = await harness.request("POST", "/application-onboarding/imports", json=payload)
    assert response.status_code == 409, response.text
    assert not harness.store.data["application"] and not harness.store.data["contract"]


@pytest.mark.asyncio
async def test_import_audit_failure_rolls_back_every_draft(harness):
    body = submission()
    response = await harness.request(
        "POST", "/application-onboarding/plans", json={"submission": body, "discovery_mode": "LIVE"}
    )
    harness.store.audit_failure = True
    response = await harness.request(
        "POST",
        "/application-onboarding/imports",
        json={
            "submission": body,
            "plan_hash": response.json()["plan_hash"],
            "expected_application_revision": 0,
        },
    )
    assert response.status_code == 500
    assert not any(
        harness.store.data[kind] for kind in ["application", "source", "product", "contract"]
    )


@pytest.mark.asyncio
async def test_import_does_not_overwrite_active_configuration(harness):
    await published(harness)
    body = submission()
    body["manifest"]["application"]["name"] = "Changed active name"
    response = await harness.request(
        "POST", "/application-onboarding/plans", json={"submission": body, "discovery_mode": "LIVE"}
    )
    assert response.status_code == 200 and not response.json()["valid"], response.text
    assert any(issue["code"] == "ACTIVE_CONFIGURATION_DRIFT" for issue in response.json()["issues"])


@pytest.mark.asyncio
async def test_manifest_capacity_gap_fails_closed_without_truncating_a_plan(harness):
    body = submission()
    source = deepcopy(body["manifest"]["sources"][0])
    body["manifest"]["sources"].extend({**source, "key": f"source-{index}"} for index in range(100))
    response = await harness.request(
        "POST", "/application-onboarding/plans", json={"submission": body}
    )
    assert response.status_code == 503
    assert harness.discovery.calls == 0 and not harness.engine.mutations
