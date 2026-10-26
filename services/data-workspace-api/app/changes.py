# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, authorized freshness manifests over committed native owner outboxes."""

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from .decision_service import DecisionPolicy


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def change_manifest(ctx, connector, repository, bindings):
    catalog = await connector.catalog(ctx)
    policy = DecisionPolicy(bindings)
    binding = policy.bindings.get(str(ctx.owner_id))
    allowed = binding is not None and binding.org_id == ctx.org_id
    authorization = signature(
        {
            "owner": str(ctx.owner_id),
            "org": str(ctx.org_id),
            "admin": ctx.admin,
            "catalog": sorted(catalog, key=lambda p: p["id"]),
            "assignment": binding.model_dump(mode="json") if allowed else None,
        }
    )
    reader = getattr(connector, "decision_events", None)
    versions, unavailable = [], []
    slots = asyncio.Semaphore(4)

    async def read(warehouse_id, producer):
        async with slots:
            try:
                page = await reader(
                    ctx,
                    producer,
                    {
                        "org_id": str(ctx.org_id),
                        "warehouse_id": str(warehouse_id),
                        "since": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                        "limit": 1,
                    },
                )
                revision = page.get("source_revision") if isinstance(page, dict) else None
                if (
                    not isinstance(revision, dict)
                    or revision.get("org_id") != str(ctx.org_id)
                    or revision.get("warehouse_id") != str(warehouse_id)
                    or revision.get("producer") != producer
                    or not re.fullmatch(r"[a-f0-9]{64}", str(revision.get("token", "")))
                ):
                    raise HTTPException(502, "Invalid source freshness marker")
                versions.append({"key": f"{ctx.org_id}:{warehouse_id}:{producer}", **revision})
            except HTTPException as error:
                if error.status_code == 401:
                    raise
                unavailable.append({"warehouse_id": str(warehouse_id), "producer": producer})

    if allowed and reader:
        tasks = [
            asyncio.create_task(read(warehouse, producer))
            for warehouse in binding.warehouse_ids
            for producer in ("sales", "warehouse", "logistics")
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    workflow = getattr(repository, "change_revision", None)
    if allowed and workflow:
        versions.append(
            {
                "key": f"{ctx.org_id}:workflow",
                "producer": "workflow",
                "warehouse_id": None,
                "org_id": str(ctx.org_id),
                "token": await workflow(ctx),
            }
        )
    return {
        "schema_version": 1,
        "principal_id": str(ctx.owner_id),
        "org_id": str(ctx.org_id),
        "authorization_stamp": authorization,
        "enabled": bool(allowed and reader),
        "complete": bool(allowed and reader and not unavailable),
        "versions": sorted(versions, key=lambda v: v["key"]),
        "unavailable": unavailable,
        "checked_at": datetime.now(UTC).isoformat(),
        "poll_after_seconds": 8,
        "coverage": "assigned_warehouses_native_api_events",
    }
