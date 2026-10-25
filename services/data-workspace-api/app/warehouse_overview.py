# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit administrative coverage across reviewed organization scopes.

Each source read keeps its own org equality filter and actual caller grant. The
default remains a single organization; scope never comes from browser input.
"""

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field

from .operations import planning_report
from .schemas import DTO, PlanningRequest, ProductRef


class WarehouseOverviewRequest(DTO):
    horizon: int = Field(default=7, ge=1, le=90, strict=True)


def overview_organizations(ctx):
    if not ctx.admin:
        raise HTTPException(403, "Administrative cross-scope overview required")
    raw = json.loads(os.environ.get("FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS", "[]"))
    if not isinstance(raw, list) or len(raw) > 20:
        raise ValueError("Overview organizations must be an explicitly reviewed bounded list")
    return list(dict.fromkeys([ctx.org_id, *(UUID(value) for value in raw)]))


async def warehouse_overview(body, ctx, connector, repository, source_names):
    organizations = overview_organizations(ctx)
    catalog = await connector.catalog(ctx)
    refs = {}
    for role in (
        "records_warehouses",
        "stock",
        "catalog",
        "open_orders",
        "demand",
        "inbound",
        "commitments",
        "receipts",
        "schedule",
        "capacity",
        "preparation",
    ):
        matches = [p for p in catalog if p["name"] == source_names[role]]
        if len(matches) > 1:
            raise HTTPException(422, "Ambiguous overview source")
        if matches:
            refs[role] = ProductRef(
                data_product_id=matches[0]["id"],
                contract_version_id=matches[0]["contract_version_id"],
            )
    required = {
        "records_warehouses",
        "stock",
        "catalog",
        "open_orders",
        "demand",
        "inbound",
        "commitments",
    }
    if not required <= refs.keys():
        raise HTTPException(403, "Complete overview sources are not currently authorized")
    fields = {
        "records_warehouses": ["warehouse_id", "warehouse_code", "warehouse_name", "status"],
        "stock": [
            "warehouse_id",
            "sku",
            "uom",
            "on_hand_quantity",
            "reserved_quantity",
            "available_quantity",
        ],
        "catalog": ["warehouse_id", "sku", "product_name", "uom"],
        "open_orders": [
            "order_id",
            "order_number",
            "warehouse_id",
            "status",
            "order_line_id",
            "sku",
            "uom",
            "ordered_quantity",
            "requested_handoff_at",
        ],
    }
    slots = asyncio.Semaphore(2)

    async def organization(org_id):
        async with slots:
            scoped = replace(ctx, org_id=org_id)
            facts = {}
            for role, columns in fields.items():
                _, facts[role] = await connector.rows(scoped, refs[role], columns)
            warehouses = facts["records_warehouses"]
            items = []
            for warehouse in warehouses:
                identifier = str(warehouse["warehouse_id"])
                stocks = [r for r in facts["stock"] if str(r["warehouse_id"]) == identifier]
                names = {
                    (r["sku"], r["uom"]): r.get("product_name")
                    for r in facts["catalog"]
                    if str(r["warehouse_id"]) == identifier
                }
                headers = {}
                for row in facts["open_orders"]:
                    if str(row["warehouse_id"]) != identifier:
                        continue
                    header = headers.setdefault(
                        str(row["order_id"]),
                        {
                            "order_id": str(row["order_id"]),
                            "order_number": row["order_number"],
                            "deadline": row["requested_handoff_at"],
                            "status": row["status"],
                            "lines": [],
                        },
                    )
                    header["lines"].append({k: row[k] for k in ("sku", "uom", "ordered_quantity")})
                products = [
                    {
                        "sku": r["sku"],
                        "name": names.get((r["sku"], r["uom"])),
                        "uom": r["uom"],
                        "on_hand": r["on_hand_quantity"],
                        "reserved": r["reserved_quantity"],
                        "available": r["available_quantity"],
                        "shortage": None,
                    }
                    for r in stocks
                ]
                plan, sources, issue = None, [], None
                if stocks:
                    request = PlanningRequest.model_validate(
                        {
                            **{
                                k: v.model_dump()
                                for k, v in refs.items()
                                if k in PlanningRequest.model_fields
                            },
                            "warehouse_id": identifier,
                            "horizon": body.horizon,
                            "forecast_model": "seasonal_mean",
                        }
                    )
                    try:
                        plan, sources = await planning_report(
                            request, scoped, connector, repository, source_names
                        )
                        by_sku = {r["sku"]: r for r in plan["products"]}
                        for product in products:
                            product["shortage"] = by_sku[product["sku"]]["shortage"]
                    except HTTPException as error:
                        detail = error.detail
                        if (
                            error.status_code != 422
                            or not isinstance(detail, dict)
                            or detail.get("code")
                            not in {
                                "NO_DATA",
                                "STALE_HISTORY",
                                "INSUFFICIENT_HISTORY",
                                "ML_HISTORY_SHORT",
                            }
                        ):
                            raise
                        issue = {"code": detail["code"], "message": detail["message"]}
                else:
                    issue = {
                        "code": "NO_STOCK_DATA",
                        "message": "Kho chưa có số liệu tồn để tính kế hoạch; "
                        "không coi tồn kho chưa biết là 0.",
                    }
                for source in sources:
                    await connector.check(
                        scoped, ProductRef.model_validate(source["ref"]), source["columns"]
                    )
                items.append(
                    {
                        "organization_id": str(org_id),
                        "warehouse_id": identifier,
                        "warehouse_name": warehouse["warehouse_name"],
                        "warehouse_code": warehouse["warehouse_code"],
                        "warehouse_status": warehouse["status"],
                        "products": products,
                        "open_orders": list(headers.values()),
                        "planning_status": "calculated" if plan else "needs_data",
                        "planning_issue": issue,
                        "plan_read_at": plan["as_of"] if plan else None,
                    }
                )
            # Read permissions are rechecked after every warehouse, even when a
            # forecast cannot run. No partial other-scope data is returned on denial.
            for role, columns in fields.items():
                await connector.check(scoped, refs[role], columns)
            return items

    groups = await asyncio.gather(*(organization(org_id) for org_id in organizations))
    items = [item for group in groups for item in group]
    if len({item["warehouse_id"] for item in items}) != len(items):
        raise HTTPException(422, "Ambiguous warehouse identity across reviewed scopes")
    return {
        "items": items,
        "read_at": datetime.now(UTC).isoformat(),
        "horizon": body.horizon,
        "scope": "reviewed_administrative_organizations",
        "warehouse_count": len(items),
    }
