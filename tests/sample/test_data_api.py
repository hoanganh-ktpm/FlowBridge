# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Test dữ liệu mẫu: schema/CRUD/transaction/history/ingestion.

import httpx
import pytest

from .test_contract_coverage import validate_response

BASE = "/api/v1/core/data/sample_items"
KEY = {"Idempotency-Key": "sample-write"}


@pytest.mark.asyncio
async def test_schema_and_crud_responses_without_persistence(client: httpx.AsyncClient) -> None:
    schema = await client.get("/api/v1/core/data-schemas/sample_items")
    assert schema.status_code == 200
    validate_response(schema, "CollectionDefinition")
    registered = await client.post(
        "/internal/v1/core/data-schemas", headers=KEY, json=schema.json()
    )
    assert registered.status_code == 201
    validate_response(registered, "CollectionDefinition")
    assert registered.json() == schema.json()
    records = await client.get(BASE, params={"page_size": 1})
    assert records.status_code == 200
    validate_response(records, "RecordList")
    assert records.json()["total"] == 2 and len(records.json()["items"]) == 1
    record = records.json()["items"][0]
    url = BASE + "/" + record["id"]
    detail = await client.get(url)
    assert detail.status_code == 200 and detail.json() == record
    validate_response(detail, "Record")
    create = await client.post(
        BASE, headers=KEY, json={"data": {"name": "New sample", "status": "draft"}}
    )
    assert create.status_code == 201
    validate_response(create, "Record")
    assert create.json()["data"]["name"] == "New sample"
    assert create.json()["version"] == 1
    assert (await client.get(BASE + "/" + create.json()["id"])).status_code == 404
    update = await client.patch(url, json={"expected_version": 2, "data": {"status": "inactive"}})
    assert update.status_code == 200
    validate_response(update, "Record")
    assert update.json()["version"] == 3
    assert update.json()["data"] == {**record["data"], "status": "inactive"}
    deletion = await client.delete(url, params={"expected_version": 2})
    assert deletion.status_code == 204 and deletion.content == b""
    assert (await client.get(url)).json() == record
    assert (await client.get(BASE)).json()["total"] == 2


@pytest.mark.asyncio
async def test_transaction_and_history_sample_contract(client: httpx.AsyncClient) -> None:
    result = await client.post(
        "/internal/v1/core/data/transactions",
        headers=KEY,
        json={
            "operations": [
                {
                    "operation": "create",
                    "collection": "sample_items",
                    "record_id": "REC-NEW",
                    "data": {"name": "Transaction sample", "status": "draft"},
                },
                {
                    "operation": "update",
                    "collection": "sample_items",
                    "record_id": "REC-SAMPLE",
                    "expected_version": 2,
                    "data": {"status": "inactive"},
                },
            ]
        },
    )
    assert result.status_code == 200
    validate_response(result, "TransactionResult")
    assert [r["id"] for r in result.json()["records"]] == ["REC-NEW", "REC-SAMPLE"]
    history = await client.get(BASE + "/REC-SAMPLE/history", params={"page_size": 1})
    assert history.status_code == 200
    validate_response(history, "RecordHistoryPage")
    assert history.json()["items"][0]["version"] == 2
    assert history.json()["next_before_version"] == 2
    older = await client.get(BASE + "/REC-SAMPLE/history", params={"before_version": 2})
    validate_response(older, "RecordHistoryPage")
    assert [r["version"] for r in older.json()["items"]] == [1]
    assert older.json()["next_before_version"] is None


@pytest.mark.asyncio
async def test_data_validation_filters_and_errors(client: httpx.AsyncClient) -> None:
    selected = await client.get(
        BASE, params={"filters": '[{"field":"status","op":"eq","value":"active"}]'}
    )
    assert selected.status_code == 200 and selected.json()["total"] == 1
    assert selected.json()["items"][0]["id"] == "REC-SAMPLE"
    bad_requests = [
        ("GET", BASE + "?page_size=101", None),
        ("GET", BASE + "?filters=not-json", None),
        ("GET", BASE + '?filters=[{"field":"secret","op":"eq","value":"x"}]', None),
        ("POST", BASE, {"data": {"name": "Missing status"}}),
        ("POST", BASE, {"data": {"name": "x", "status": "active", "extra": True}}),
        ("PATCH", BASE + "/REC-SAMPLE", {"expected_version": True, "data": {}}),
    ]
    for method, url, body in bad_requests:
        response = await client.request(method, url, headers=KEY, json=body)
        assert response.status_code == 422, response.text
        validate_response(response, "ErrorResponse")
    missing_key = await client.post(BASE, json={"data": {"name": "x", "status": "active"}})
    assert missing_key.status_code == 422
    stale = await client.patch(BASE + "/REC-SAMPLE", json={"expected_version": 1, "data": {}})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    missing = await client.get(BASE + "/UNKNOWN", headers={"X-Correlation-Id": "sample-check"})
    assert missing.status_code == 404
    assert missing.json()["request_id"] == missing.headers["x-correlation-id"] == "sample-check"
    malformed = await client.post(
        BASE, headers={**KEY, "Content-Type": "application/json"}, content=b'{"data":'
    )
    assert malformed.status_code == 400
