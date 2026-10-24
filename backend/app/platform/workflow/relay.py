"""Authenticated Hasura header ingress -> normalized body HMAC -> Event Router.

IT trigger bindings describe sources/tables/transitions. Workflow subscriptions
remain separate and decide START/CORRELATE. No business database connection here.
"""

import hashlib
import hmac
import json
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.platform.http.errors import CoreError, ErrorCode


def normalize_hasura_event(body, bindings, source_hint=None):
    trigger = body.get("trigger", {}).get("name")
    rule = bindings.get(trigger)
    if rule is None:
        raise CoreError(ErrorCode.FORBIDDEN)
    event = body.get("event", {})
    table = body.get("table", {})
    data = event.get("data", {})
    row, old = data.get("new"), data.get("old")
    if (
        (body.get("source") or source_hint) != rule["source"]
        or table != {"schema": rule["schema"], "name": rule["table"]}
        or event.get("op") != rule["operation"]
        or not isinstance(row, dict)
    ):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    if row.get("org_id") not in rule["org_ids"]:
        raise CoreError(ErrorCode.FORBIDDEN)
    status = row.get("status")
    # Unrelated/old transitions and workflow-origin updates do not fan out.
    if (
        status != rule["status"]
        or (isinstance(old, dict) and old.get("status") == status)
        or row.get("triggered_by") == "workflow"
    ):
        return None
    if isinstance(old, dict) and row.get("version", 0) <= old.get("version", 0):
        return None
    try:
        event_id = str(UUID(body["id"]))
        order_id = str(UUID(row[rule["order_field"]]))
    except (ValueError, KeyError, TypeError):
        raise CoreError(ErrorCode.VALIDATION_ERROR) from None
    return {
        "event_id": event_id,
        "event_type": rule["event_type"],
        "payload": {"order_id": order_id, "status": status},
    }


async def forward_hasura(raw, header, configuration, http, source_hint=None):
    secret = configuration.hasura_relay_header_secret.get_secret_value()
    if not secret or not header or not hmac.compare_digest(header, secret):
        raise CoreError(ErrorCode.UNAUTHENTICATED)
    if len(raw) > 1_000_000:
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError()
    except ValueError:
        raise CoreError(ErrorCode.VALIDATION_ERROR) from None
    event = normalize_hasura_event(body, configuration.hasura_relay_bindings, source_hint)
    if event is None:
        return {"ignored": True}
    parsed = urlsplit(configuration.hasura_relay_target)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    ):
        raise CoreError(ErrorCode.FORBIDDEN)
    payload = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(
        configuration.event_webhook_secret.get_secret_value().encode(), payload, hashlib.sha256
    ).hexdigest()
    try:
        response = await http.post(
            configuration.hasura_relay_target,
            content=payload,
            headers={"Content-Type": "application/json", "X-Event-Signature": signature},
            timeout=45,
            follow_redirects=False,
        )
        if response.status_code != 202:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return response.json()
    except httpx.TimeoutException:
        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
    except (httpx.HTTPError, ValueError):
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
