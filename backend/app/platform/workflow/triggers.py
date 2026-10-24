# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge, licensed under GNU GPL version 3 or later.
# You may redistribute and modify it under that license. It is provided without
# any warranty, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE for the full terms.

# Hasura event-trigger provisioning for workflow event subscriptions.
# Control-plane only: uses HasuraMetadataClient with the admin secret.
# Never used at workflow runtime. Payloads carry business keys only;
# runtime authorization stays with Keycloak + Core grants.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class WorkflowTriggerSpec:
    name: str
    source: str
    schema: str
    table: str
    operations: tuple[str, ...]  # subset of INSERT/UPDATE/DELETE
    webhook_url: str
    columns: tuple[str, ...] = ()
    headers: tuple[dict[str, str], ...] = ()
    allow_local_callback: bool = False


def trigger_payload(spec: WorkflowTriggerSpec, secret_header: str | None = None) -> dict[str, Any]:
    url = urlsplit(spec.webhook_url)
    local = (
        spec.allow_local_callback
        and url.scheme == "http"
        and url.hostname == "host.docker.internal"
    )
    if not spec.name.strip() or not (spec.webhook_url.startswith("https://") or local):
        raise ValueError("Trigger needs a name and an HTTPS webhook URL")
    ops = {op.upper() for op in spec.operations}
    if not ops <= {"INSERT", "UPDATE", "DELETE"} or not ops:
        raise ValueError("Operations must be a non-empty subset of INSERT/UPDATE/DELETE")
    definition: dict[str, Any] = {
        "name": spec.name,
        "source": spec.source,
        "table": {"schema": spec.schema, "name": spec.table},
        "webhook": spec.webhook_url,
        "enable_manual": False,
        "retry_conf": {"num_retries": 5, "interval_sec": 2, "timeout_sec": 60},
        **{operation.lower(): {"columns": list(spec.columns) or "*"} for operation in sorted(ops)},
    }
    if secret_header:
        raise ValueError("A static Hasura header is not a body HMAC; use a trusted signing relay")
    elif spec.headers:
        definition["headers"] = list(spec.headers)
    args: dict[str, Any] = {"source": spec.source, **definition}
    return {"type": "pg_create_event_trigger", "version": 1, "args": args}


async def provision_trigger(client: Any, spec: WorkflowTriggerSpec) -> None:
    payload = trigger_payload(spec)
    await client._metadata("pg_create_event_trigger", payload["args"])


async def drop_trigger(client: Any, source: str, name: str) -> None:
    await client._metadata("pg_delete_event_trigger", {"source": source, "name": name})
