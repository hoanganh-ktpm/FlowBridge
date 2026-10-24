"""Negative security checks against an explicitly retained, successful Workflow fixture."""

import os
from pathlib import Path

import httpx
import pytest
from scripts.e2e_runtime import NativeRuntime
from tests.e2e.workflow_fixture import prepare_identities, workflow_tokens

pytestmark = pytest.mark.e2e


def test_real_workflow_rejects_unauthorized_reads_admin_spoof_and_invalid_hmac():
    directory = os.environ.get("FLOWBRIDGE_WORKFLOW_SECURITY_RUN_DIR")
    if not directory:
        pytest.skip("Set FLOWBRIDGE_WORKFLOW_SECURITY_RUN_DIR to a successful owned Workflow run")
    assert Path(directory).name.startswith("workflow-data-")
    runtime = NativeRuntime(directory)
    assert runtime.resources["evidence"].get("workflow_end_and_event_start_correlate") is True
    credentials = workflow_tokens(prepare_identities())
    try:
        runtime.start("api")
        with httpx.Client(base_url=runtime.base_url, timeout=40) as client:
            admin = {"Authorization": "Bearer " + credentials["it"]}
            product_id = runtime.resources["imports"]["warehouse"]["data_product_ids"][0]
            response = client.get("/api/v1/core/data-products/" + product_id, headers=admin)
            assert response.status_code == 200
            payload = {
                "product_key": response.json()["product_key"],
                "contract_version": "1.0.0",
                "columns": ["id"],
                "limit": 1,
            }
            for label in ("it", "outsider"):
                response = client.post(
                    "/api/v1/workflows/actions/data.query/execute",
                    headers={"Authorization": "Bearer " + credentials[label]},
                    json=payload,
                )
                assert response.status_code == 403
            response = client.post(
                "/api/v1/workflows/definitions",
                json={"name": "denied", "bpmn_xml": "<invalid/>"},
                headers={
                    "Authorization": "Bearer " + credentials["outsider"],
                    "X-Role": "PLATFORM_ADMIN",
                    "X-Hasura-Role": "admin",
                },
            )
            assert response.status_code == 403
            response = client.post(
                "/api/v1/workflows/events",
                content=b'{"event_id":"forged","event_type":"none","payload":{}}',
                headers={"Content-Type": "application/json", "X-Event-Signature": "0" * 64},
            )
            assert response.status_code == 401
            runtime.evidence("negative_authorization_and_invalid_hmac", True)
    finally:
        runtime.close()
