# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge, licensed under GNU GPL version 3 or later.
# You may redistribute and modify it under that license. It is provided without
# any warranty, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE for the full terms.

# Event router: Hasura webhooks -> normalized durable inbox -> Flowable.
# One event may start or resume many workflows. Deduplicated by event id,
# guarded against self-triggered loops via the source marker.

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.platform.http.errors import CoreError, ErrorCode
from app.platform.workflow.flowable import FlowablePort


@dataclass(frozen=True)
class EventSubscription:
    subscription_id: str
    event_type: str  # e.g. "inventory.updated"
    workflow_key: str
    mode: str  # "START" or "CORRELATE"
    message_name: str | None = None
    source_marker: str = "workflow"


@dataclass
class EventRouter:
    engine: FlowablePort
    webhook_secret: str = ""
    subscriptions: list[EventSubscription] = field(default_factory=list)
    inbox: Any = None

    def subscribe(self, subscription: EventSubscription) -> None:
        if subscription.mode not in {"START", "CORRELATE"}:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if any(item.subscription_id == subscription.subscription_id for item in self.subscriptions):
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        if subscription.mode == "CORRELATE" and not subscription.message_name:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        self.subscriptions.append(subscription)

    def verify(self, raw_body: bytes, signature: str | None) -> None:
        if not self.webhook_secret:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        expected = hmac.new(self.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if (
            signature is None
            or not re.fullmatch(r"[0-9a-f]{64}", signature)
            or not hmac.compare_digest(expected, signature)
        ):
            raise CoreError(ErrorCode.UNAUTHENTICATED)

    def _matches(self, event_type: str) -> list[EventSubscription]:
        return [s for s in self.subscriptions if s.event_type == event_type]

    async def receive(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        event_id = event.get("event_id")
        event_type = event.get("event_type")
        if (
            not isinstance(event_id, str)
            or not event_id
            or not isinstance(event_type, str)
            or not event_type
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if not isinstance(event.get("payload"), dict) or len(event_id) > 200:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        payload = dict(event["payload"])
        if (
            payload.get("business_key")
            and payload.get("order_id")
            and payload["business_key"] != payload["order_id"]
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if any(
            not isinstance(payload[name], str) or not 1 <= len(payload[name]) <= 200
            for name in ("order_id", "status")
            if name in payload
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if payload.get("triggered_by") == "workflow":
            return [{"event_id": event_id, "skipped_loop": True}]
        outcomes: list[dict[str, Any]] = []
        for subscription in self._matches(event_type):
            business_key = payload.get("business_key") or payload.get("order_id")
            if not isinstance(business_key, str) or not 1 <= len(business_key) <= 200:
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            if set(payload) - {"business_key", "order_id", "status"}:
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            if self.inbox is None:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            digest = hashlib.sha256(
                json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            dispatch_key = hashlib.sha256(
                (subscription.subscription_id + ":" + event_id).encode()
            ).hexdigest()

            async def dispatch(
                subscription=subscription, dispatch_key=dispatch_key, business_key=business_key
            ):
                variables = {
                    **payload,
                    "triggered_by": "workflow",
                    "internal_start_ref": dispatch_key,
                }
                if subscription.mode == "START":
                    instance = await self.engine.start_process(
                        subscription.workflow_key, business_key, variables
                    )
                else:
                    instance = await self.engine.message_event(
                        subscription.message_name,
                        business_key,
                        variables,
                        workflow_key=subscription.workflow_key,
                    )
                return {
                    "event_id": event_id,
                    "subscription_id": subscription.subscription_id,
                    "instance_id": instance.get("id"),
                }

            outcome = await self.inbox.once(dispatch_key, digest, dispatch)
            outcomes.append(outcome)
        return outcomes
