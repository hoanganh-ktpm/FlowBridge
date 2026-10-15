# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Test sự kiện mẫu: publish/notification/audit theo hợp đồng.

import httpx
import pytest

from .test_contract_coverage import validate_response

KEY = {"Idempotency-Key": "sample-events"}


@pytest.mark.asyncio
async def test_event_notifications_and_audit_are_sample_responses(
    client: httpx.AsyncClient,
) -> None:
    event = await client.post(
        "/internal/v1/core/events",
        headers=KEY,
        json={
            "event_id": "EVENT-CLIENT",
            "event_type": "sample.created",
            "schema_version": "1.0",
            "occurred_at": "2026-10-15T03:00:00Z",
            "tenant_id": "TENANT-SAMPLE",
            "producer": "sample",
            "correlation_id": "CORR-SAMPLE",
            "aggregate_type": "sample_item",
            "aggregate_id": "REC-SAMPLE",
            "aggregate_version": 2,
            "data": {},
        },
    )
    assert event.status_code == 202
    validate_response(event, "EventAccepted")
    assert event.json() == {"event_id": "EVENT-CLIENT", "accepted": True}
    notes = await client.get("/api/v1/core/notifications")
    assert notes.status_code == 200
    validate_response(notes, "NotificationList")
    note = notes.json()["items"][0]
    assert note["read"] is False
    read = await client.post("/api/v1/core/notifications/" + note["id"] + "/read", headers=KEY)
    assert read.status_code == 200
    validate_response(read, "Notification")
    assert read.json() == {**note, "read": True}
    assert (await client.get("/api/v1/core/notifications")).json() == notes.json()
    audit = await client.get(
        "/api/v1/core/audit-entries", params={"resource_id": note["resource_id"]}
    )
    assert audit.status_code == 200
    validate_response(audit, "AuditList")
    assert audit.json()["items"][0]["resource_id"] == note["resource_id"]
    assert (await client.get("/api/v1/core/audit-entries?resource_id=OTHER")).json()["total"] == 0
    assert (
        await client.post("/api/v1/core/notifications/UNKNOWN/read", headers=KEY)
    ).status_code == 404
