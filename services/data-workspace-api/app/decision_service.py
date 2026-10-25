# SPDX-License-Identifier: GPL-3.0-or-later
"""Governed decision dossiers; workflow authority remains in Core/Flowable."""

from copy import deepcopy
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, ValidationError

from .schemas import DTO, SourceRead

Role = Literal["coordinator", "warehouse", "logistics", "manager"]


class ActorBinding(DTO):
    org_id: UUID
    warehouse_ids: list[UUID] = Field(min_length=1, max_length=20)
    roles: list[Role] = Field(min_length=1, max_length=4)


class DecisionPolicy:
    """Deployment-owned operational assignments, additional to current Core read grants."""

    def __init__(self, bindings):
        self.bindings = {
            str(UUID(key)): ActorBinding.model_validate(value) for key, value in bindings.items()
        }

    def roles(self, ctx, warehouse_id):
        binding = self.bindings.get(str(ctx.owner_id))
        if (
            binding is None
            or binding.org_id != ctx.org_id
            or UUID(str(warehouse_id)) not in binding.warehouse_ids
        ):
            return set()
        return set(binding.roles)

    def roster(self, ctx, warehouse_id):
        warehouse = UUID(str(warehouse_id))
        if "coordinator" not in self.roles(ctx, warehouse):
            raise HTTPException(403, "No operational coordinator assignment for this warehouse")
        result = [{"principal_id": str(ctx.owner_id), "role": "coordinator"}]
        verifier_ids = {str(ctx.owner_id)}
        for role in ("warehouse", "logistics"):
            found = [
                {"principal_id": key, "role": role}
                for key, binding in self.bindings.items()
                if binding.org_id == ctx.org_id
                and warehouse in binding.warehouse_ids
                and role in binding.roles
                and key != str(ctx.owner_id)
            ]
            if not found:
                raise HTTPException(
                    503, "Configure independent Warehouse and Logistics participants"
                )
            result.extend(found)
            verifier_ids.update(item["principal_id"] for item in found)
        managers = [
            {"principal_id": key, "role": "manager"}
            for key, binding in self.bindings.items()
            if binding.org_id == ctx.org_id
            and warehouse in binding.warehouse_ids
            and "manager" in binding.roles
            and key not in verifier_ids
        ]
        if not managers:
            raise HTTPException(503, "Configure a manager separate from proposal and verification")
        return sorted([*result, *managers], key=lambda item: (item["role"], item["principal_id"]))

    def require_case(self, ctx, case):
        roles = self.roles(ctx, case["warehouse_id"])
        assigned = {
            item["role"]
            for item in case["participants"]
            if item["principal_id"] == str(ctx.owner_id)
        }
        if not roles.intersection(assigned):
            raise HTTPException(403, "Operational case assignment is no longer effective")


class DecisionService:
    def __init__(self, repository, results, policy, reauthorize):
        self.repository, self.results = repository, results
        self.policy, self.reauthorize = policy, reauthorize

    async def permitted_payload(self, ctx, payload):
        # SourceRead rejects arbitrary fields/URLs and enforces bounded column identifiers.
        sources = payload.get("sources", [])
        if not sources:
            raise HTTPException(409, "Decision evidence has no governed source references")
        try:
            validated = [
                SourceRead.model_validate(item).model_dump(mode="json") for item in sources
            ]
        except ValidationError:
            raise HTTPException(
                409, "Stored decision source references require recomputation"
            ) from None
        await self.reauthorize(ctx, {"sources": validated})

    async def snapshot(self, ctx, simulation_id, order_id):
        run = await self.results.get("simulation", simulation_id, ctx)
        await self.permitted_payload(ctx, run)
        assumptions = run["evidence"]["assumptions"]
        warehouse_id = UUID(assumptions["warehouse_id"])
        if (
            not run.get("decision")
            or run["evidence"].get("metric_semantics_version") != "physical_supply_deficit_v1"
        ):
            raise HTTPException(
                409, "Recompute the saved scenario before creating a decision revision"
            )
        selected = [
            order
            for order in run["baseline"].get("orders", [])
            if order["order_id"] == str(order_id)
        ]
        if len(selected) != 1:
            raise HTTPException(422, "Choose an unallocated order contained in this saved scenario")
        if not any(line["sku"] == assumptions["sku"] for line in selected[0]["lines"]):
            raise HTTPException(422, "The scenario product does not belong to the selected order")
        forecast = await self.results.get("forecast", UUID(assumptions["forecast_run_id"]), ctx)
        await self.permitted_payload(ctx, forecast)
        scenario, shared_forecast = deepcopy(run), deepcopy(forecast)
        for name in ("baseline", "proposed", "reference"):
            if scenario.get(name):
                scenario[name]["orders"] = [
                    order
                    for order in scenario[name].get("orders", [])
                    if order["order_id"] == str(order_id)
                ]
        shared_forecast["points"] = [
            point for point in shared_forecast["points"] if point["item_id"] == assumptions["sku"]
        ]
        sources = [*run["sources"], *forecast["sources"]]
        payload = {
            "scenario": scenario,
            "forecast": shared_forecast,
            "sources": sources,
            "scope": {
                "warehouse_id": str(warehouse_id),
                "order_id": str(order_id),
                "sku": assumptions["sku"],
            },
            "requires_revalidation": True,
        }
        return warehouse_id, payload

    async def create(self, ctx, body, key):
        warehouse, payload = await self.snapshot(ctx, body.simulation_run_id, body.order_id)
        roster = self.policy.roster(ctx, warehouse)
        value = await self.repository.create(
            ctx,
            order_id=body.order_id,
            warehouse_id=warehouse,
            simulation_id=body.simulation_run_id,
            title=body.title,
            roster=roster,
            payload=payload,
            key=key,
        )
        await self.permitted_payload(ctx, value["revision"]["payload"])
        return value

    async def get(self, ctx, identifier):
        value = await self.repository.get(ctx, identifier)
        self.policy.require_case(ctx, value)
        await self.permitted_payload(ctx, value["revision"]["payload"])
        return value

    async def list(self, ctx):
        ids = await self.repository.list(ctx)
        items, limited = [], False
        for identifier in ids[:100]:
            try:
                value = await self.get(ctx, identifier)
            except HTTPException as error:
                if error.status_code in {403, 404}:
                    limited = True
                    continue
                raise
            items.append(
                {
                    key: value[key]
                    for key in (
                        "id",
                        "title",
                        "warehouse_id",
                        "order_id",
                        "status",
                        "version",
                        "current_revision",
                        "updated_at",
                        "coordinator_id",
                    )
                }
            )
        return {
            "items": items,
            "has_more": len(ids) > 100,
            "warnings": ["Some case sources/assignments are no longer authorized"]
            if limited
            else [],
        }

    async def revise(self, ctx, identifier, body, key):
        case = await self.get(ctx, identifier)
        if "coordinator" not in self.policy.roles(ctx, case["warehouse_id"]):
            raise HTTPException(403, "Only the coordinator can revise this case")
        warehouse, payload = await self.snapshot(
            ctx, body.simulation_run_id, UUID(case["order_id"])
        )
        if str(warehouse) != case["warehouse_id"]:
            raise HTTPException(
                409, "A decision revision cannot move the case to another warehouse"
            )
        value = await self.repository.revise(
            ctx,
            identifier,
            simulation_id=body.simulation_run_id,
            expected_version=body.expected_version,
            payload=payload,
            key=key,
        )
        await self.permitted_payload(ctx, value["revision"]["payload"])
        return value

    async def history(self, ctx, identifier):
        await self.get(ctx, identifier)
        rows = await self.repository.history(ctx, identifier)
        for row in rows:
            await self.permitted_payload(ctx, row["payload"])
        return {"items": rows[:100], "has_more": len(rows) > 100}

    async def timeline(self, ctx, identifier):
        await self.get(ctx, identifier)
        # Older events can reference a source whose permission has since changed.
        rows = await self.repository.timeline(ctx, identifier)
        revision_ids = {UUID(row["revision_id"]) for row in rows if row["revision_id"]}
        referenced = await self.repository.revisions_for(ctx, identifier, revision_ids)
        for row in referenced:
            await self.permitted_payload(ctx, row["payload"])
        return {"items": rows[:500], "has_more": len(rows) > 500}
