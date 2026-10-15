# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Test tệp/ingestion mẫu: upload/download/search và job tổng hợp.

import hashlib

import httpx
import pytest

from .test_contract_coverage import validate_response

KEY = {"Idempotency-Key": "sample-files"}


@pytest.mark.asyncio
async def test_ingestion_returns_sample_job_and_synthetic_result(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/internal/v1/core/ingestion-jobs",
        headers=KEY,
        json={
            "collection": "sample_items",
            "source_kind": "IT",
            "source_id": "source-sample",
            "observed_at": "2026-10-15T03:00:00Z",
            "schema_version": 1,
            "records": [{"name": "Sample", "status": "active"}],
        },
    )
    assert created.status_code == 202
    validate_response(created, "PlatformJob")
    assert created.json()["status"] == "queued"
    assert created.json()["result"] is None and created.json()["error"] is None
    job = await client.get("/internal/v1/core/ingestion-jobs/" + created.json()["job_id"])
    assert job.status_code == 200
    validate_response(job, "PlatformJob")
    assert job.json()["status"] == "succeeded"
    assert job.json()["result"]["received"] == job.json()["result"]["accepted"] == 1
    assert (await client.get("/internal/v1/core/ingestion-jobs/UNKNOWN")).status_code == 404


@pytest.mark.asyncio
async def test_file_metadata_download_and_search_are_consistent(client: httpx.AsyncClient) -> None:
    listing = await client.get("/api/v1/core/files", params={"resource_id": "REC-SAMPLE"})
    assert listing.status_code == 200
    validate_response(listing, "FileList")
    assert listing.json()["total"] == 2
    file = listing.json()["items"][0]
    detail = await client.get("/api/v1/core/files/" + file["id"])
    assert detail.status_code == 200 and detail.json() == file
    validate_response(detail, "File")
    download = await client.get("/api/v1/core/files/" + file["id"] + "/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith(file["content_type"])
    assert len(download.content) == file["size"]
    assert hashlib.sha256(download.content).hexdigest() == file["sha256"]
    assert "attachment" in download.headers["content-disposition"]
    pending = await client.get("/api/v1/core/files/FILE-PENDING/download")
    assert pending.status_code == 409
    validate_response(pending, "ErrorResponse")
    search = await client.get("/api/v1/core/files/search", params={"q": "Synthetic"})
    assert search.status_code == 200
    validate_response(search, "FileSearchPage")
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["file"] == file
    assert "Synthetic" in search.json()["items"][0]["excerpt"]
    assert (await client.get("/api/v1/core/files/search", params={"q": "no-match"})).json()[
        "total"
    ] == 0
    assert (await client.get("/api/v1/core/files/search", params={"q": " "})).status_code == 422


@pytest.mark.asyncio
async def test_upload_reads_real_multipart_but_does_not_persist(client: httpx.AsyncClient) -> None:
    content = b"new synthetic upload\n"
    upload = await client.post(
        "/api/v1/core/files",
        headers=KEY,
        files={"file": ("new.txt", content, "text/plain")},
        data={"metadata": '{"resource_id":"REC-SAMPLE","tenant_id":"FORGED"}'},
    )
    assert upload.status_code == 202
    validate_response(upload, "File")
    result = upload.json()
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert result["size"] == len(content) and result["scan_status"] == "pending"
    assert result["metadata"] == {"resource_id": "REC-SAMPLE"}
    assert (await client.get("/api/v1/core/files/" + result["id"])).status_code == 404
    for metadata in ["bad-json", "[]", "null"]:
        bad = await client.post(
            "/api/v1/core/files",
            headers=KEY,
            files={"file": ("a.txt", b"x")},
            data={"metadata": metadata},
        )
        assert bad.status_code == 422
        validate_response(bad, "ErrorResponse")
    duplicate = await client.post(
        "/api/v1/core/files",
        headers=KEY,
        files=[("file", ("a.txt", b"a")), ("file", ("b.txt", b"b"))],
    )
    assert duplicate.status_code in {400, 422}
    assert (await client.get("/api/v1/core/files")).json()["total"] == 2
