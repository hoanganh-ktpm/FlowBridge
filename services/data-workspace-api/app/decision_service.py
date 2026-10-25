# SPDX-License-Identifier: GPL-3.0-or-later
"""Governed decision dossiers; workflow authority remains in Core/Flowable."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, ValidationError

from .decision_events import validate_event
from .decision_impact import assess_change
from .decision_presentation import presentation, resolve_intent
from .handoff_plan import HandoffPlan
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
    def __init__(
        self,
        repository,
        results,
        policy,
        reauthorize,
        inspector=None,
        workflow=None,
        event_reader=None,
    ):
        self.repository, self.results = repository, results
        self.policy, self.reauthorize = policy, reauthorize
        self.inspector, self.workflow = inspector, workflow
        self.event_reader = event_reader

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

    async def snapshot(self, ctx, simulation_id, order_id, handoff_plan=None):
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
        if handoff_plan:
            payload["handoff_plan"] = handoff_plan.model_dump(mode="json")
        if self.inspector:
            baseline = await self.inspector(ctx, payload)
            payload["validation_baseline"] = baseline
            payload["sources"].extend(baseline["sources"])
        return warehouse_id, payload

    async def checked(self, ctx, case):
        if self.workflow is None or self.inspector is None:
            raise HTTPException(503, "Configure the governed Core workflow service first")
        payload, revision = case["revision"]["payload"], UUID(case["revision"]["id"])
        try:
            proof = await self.inspector(ctx, payload)
            baseline = payload.get("validation_baseline")
            impact = assess_change(baseline, proof)
            if impact["requires_revision"]:
                await self.repository.record_check(
                    ctx, UUID(case["id"]), revision, "STALE", {**proof, "impact": impact}
                )
                raise HTTPException(
                    409,
                    {
                        "code": "DECISION_REQUIRES_REVISION",
                        "message": "Source conditions changed; create and verify a new revision",
                        "impact": impact,
                    },
                )
        except HTTPException as error:
            outcome = (
                "DENIED"
                if error.status_code in {401, 403}
                else "UNAVAILABLE"
                if error.status_code >= 500
                else "STALE"
            )
            if (
                not isinstance(error.detail, dict)
                or error.detail.get("code") != "DECISION_REQUIRES_REVISION"
            ):
                await self.repository.record_check(
                    ctx,
                    UUID(case["id"]),
                    revision,
                    outcome,
                    {"http_status": error.status_code, "requires_revalidation": True},
                )
            raise
        proof = {
            **proof,
            "observed_input_sha256": proof["input_sha256"],
            "input_sha256": baseline["input_sha256"],
            "impact": impact,
        }
        await self.repository.record_check(ctx, UUID(case["id"]), revision, "VALID", proof)
        return proof

    async def revalidate(self, ctx, identifier):
        case = await self.get(ctx, identifier)
        proof = await self.checked(ctx, case)
        return {
            "revision_id": case["revision"]["id"],
            "checked_at": proof["checked_at"],
            "impact": proof["impact"],
            "warehouse_confirmable": proof["warehouse_confirmable"],
            "logistics_confirmable": proof["logistics_confirmable"],
        }

    async def checks(self, ctx, identifier):
        case = await self.get(ctx, identifier)
        return await self.repository.check_history(ctx, identifier, UUID(case["revision"]["id"]))

    async def explain(self, ctx, identifier, body):
        case = await self.get(ctx, identifier)
        if case["revision"]["id"] != str(body.revision_id):
            raise HTTPException(409, "Explanation refers to an old revision")
        intent = body.intent or resolve_intent(body.question)
        workflow = None
        if intent == "tasks" and case["workflow_instance_ref"]:
            state = await self.workflow_state(ctx, identifier)
            case, workflow = state["case"], state["workflow"]
            if case["revision"]["id"] != str(body.revision_id):
                raise HTTPException(409, "Case revision changed during explanation")
        return presentation(case, intent, workflow)

    async def tasks(self, ctx):
        listing = await self.list(ctx)
        if self.workflow is None:
            raise HTTPException(503, "Workflow service is not configured")
        slots, warnings = asyncio.Semaphore(4), list(listing["warnings"])

        async def read(item):
            if item["status"] not in {"VALIDATING", "PENDING_APPROVAL"}:
                return []
            async with slots:
                try:
                    state = await self.workflow_state(ctx, UUID(item["id"]))
                except HTTPException as error:
                    if error.status_code == 401:
                        raise
                    warnings.append(
                        "Some workflow states are unconfirmed; do not assume no tasks remain"
                    )
                    return []
            if state["workflow"] is None:
                warnings.append("Some workflow starts remain unconfirmed")
                return []
            return [
                {
                    "case_id": item["id"],
                    "title": item["title"],
                    "revision_id": state["case"]["revision"]["id"],
                    "task": task,
                }
                for task in state["workflow"]["tasks"]
                if task["mine"]
            ]

        groups = await asyncio.gather(*(read(item) for item in listing["items"]))
        return {
            "items": [task for group in groups for task in group],
            "has_more": listing["has_more"],
            "warnings": sorted(set(warnings)),
        }

    async def sync_events(self, ctx, identifier):
        case = await self.get(ctx, identifier)
        if self.event_reader is None:
            raise HTTPException(503, "Configure registered native owner event read actions")
        count, warnings = 0, []
        # Replay the full case window on each explicit sync. This also recovers
        # delayed commits below an earlier cursor, with inbox dedup across users.
        since = (datetime.fromisoformat(case["created_at"]) - timedelta(minutes=1)).isoformat()
        for producer in ("sales", "warehouse", "logistics"):
            body = {
                "org_id": str(ctx.org_id),
                "warehouse_id": case["warehouse_id"],
                "since": since,
                "limit": 100,
            }
            seen = set()
            for _ in range(10):
                page = await self.event_reader(ctx, producer, body)
                if (
                    not isinstance(page, dict)
                    or type(page.get("has_more")) is not bool
                    or not isinstance(page.get("items"), list)
                    or len(page["items"]) > 100
                ):
                    raise HTTPException(502, "Invalid owner event page")
                for raw in page["items"]:
                    try:
                        value = validate_event(
                            raw, producer, ctx.org_id, UUID(case["warehouse_id"])
                        )
                    except (ValidationError, TypeError, ValueError):
                        raise HTTPException(502, "Invalid owner event summary") from None
                    if value["id"] in seen:
                        raise HTTPException(502, "Owner event pagination repeated a row")
                    seen.add(value["id"])
                    count += await self.repository.observe_source(
                        ctx, identifier, UUID(case["revision"]["id"]), value
                    )
                if not page["has_more"]:
                    break
                if not page["items"]:
                    raise HTTPException(502, "Owner event pagination is incomplete")
                last = page["items"][-1]
                body.update(after_time=last["occurred_at"], after_id=last["id"])
            else:
                warnings.append(producer + " replay exceeds 1000 events; history is incomplete")
        return {
            "new_observations": count,
            "warnings": warnings,
            "requires_source_revalidation": True,
            "message": "Events are hints; current source checks remain mandatory",
        }

    async def submit(self, ctx, identifier, body, key):
        case = await self.get(ctx, identifier)
        if "coordinator" not in self.policy.roles(ctx, case["warehouse_id"]):
            raise HTTPException(403, "Only the coordinator can submit")
        if case["revision"]["id"] != str(body.revision_id):
            raise HTTPException(409, "Submission refers to an old revision")
        proof = await self.checked(ctx, case)
        # Durable intent freezes this revision before crossing the process boundary.
        case = await self.repository.begin_submission(
            ctx, identifier, body.expected_version, body.revision_id, key
        )
        selected = {
            role: sorted(p["principal_id"] for p in case["participants"] if p["role"] == role)[0]
            for role in ("coordinator", "warehouse", "logistics", "manager")
        }
        command = {
            "org_id": str(ctx.org_id),
            "warehouse_id": case["warehouse_id"],
            "order_id": case["order_id"],
            "case_id": case["id"],
            "revision_id": str(body.revision_id),
            "revision_number": case["current_revision"],
            "participants": selected,
            "check": {
                "revision_id": str(body.revision_id),
                "input_sha256": proof["input_sha256"],
                "checked_at": proof["checked_at"],
            },
            "order_source_id": proof["order_source_id"],
            "order_contract_id": proof["order_contract_id"],
        }
        started = await self.workflow.request("/decision-runs/commands/start", command, key)
        if started["case_id"] != case["id"] or started["revision_id"] != str(body.revision_id):
            raise HTTPException(502, "Workflow returned a different revision")
        if not case["workflow_instance_ref"]:
            await self.repository.workflow_projection(
                ctx, identifier, body.revision_id, started["id"], "VALIDATING"
            )
        return await self.workflow_state(ctx, identifier)

    async def workflow_state(self, ctx, identifier):
        case = await self.get(ctx, identifier)
        if self.workflow is None:
            raise HTTPException(503, "Workflow service is not configured")
        if not case["workflow_instance_ref"]:
            return {
                "case": case,
                "workflow": None,
                "warnings": ["Not submitted or awaiting start reconciliation"],
            }
        state = await self.workflow.request(
            "/decision-runs/" + case["workflow_instance_ref"],
            params={"principal_id": str(ctx.owner_id)},
        )
        if state["revision_id"] != case["revision"]["id"]:
            raise HTTPException(409, "Workflow state belongs to another revision")
        if state["status"] != "COMPLETED":
            case = await self.repository.workflow_projection(
                ctx, identifier, UUID(state["revision_id"]), state["instance_id"], state["status"]
            )
        return {
            "case": case,
            "workflow": state,
            "warnings": ["Actual handoff still requires source proof"]
            if state["status"] == "COMPLETED" and case["status"] != "COMPLETED"
            else [],
        }

    async def reconcile_start(self, ctx, identifier):
        case = await self.get(ctx, identifier)
        if self.workflow is None:
            raise HTTPException(503, "Workflow service is not configured")
        if case["workflow_instance_ref"]:
            return await self.workflow_state(ctx, identifier)
        if case["status"] != "VALIDATING" or "coordinator" not in self.policy.roles(
            ctx, case["warehouse_id"]
        ):
            raise HTTPException(409, "No pending start owned by this coordinator")
        result = await self.workflow.request(
            "/decision-runs/commands/reconcile-start",
            {
                "org_id": str(ctx.org_id),
                "case_id": case["id"],
                "revision_id": case["revision"]["id"],
                "actor_principal_id": str(ctx.owner_id),
            },
        )
        if result.get("state") != "FOUND":
            return {
                "case": case,
                "workflow": None,
                "warnings": ["Start remains UNCONFIRMED; do not dispatch a replacement"],
            }
        run = result["run"]
        if (
            any(run.get(field) != case[field] for field in ("org_id", "warehouse_id", "order_id"))
            or run.get("case_id") != case["id"]
            or run.get("revision_id") != case["revision"]["id"]
        ):
            raise HTTPException(502, "Reconciled start belongs to a different case revision")
        await self.repository.workflow_projection(
            ctx, identifier, UUID(case["revision"]["id"]), run["id"], "VALIDATING"
        )
        return await self.workflow_state(ctx, identifier)

    async def replan(self, ctx, identifier, body, key):
        case = await self.get(ctx, identifier)
        if not self.policy.roles(ctx, case["warehouse_id"]).intersection(
            {"coordinator", "manager"}
        ):
            raise HTTPException(403, "Only coordinator or manager can request a new plan")
        if (
            self.workflow is None
            or not case["workflow_instance_ref"]
            or case["revision"]["id"] != str(body.revision_id)
        ):
            raise HTTPException(409, "No matching approved process to replan")
        result = await self.workflow.request(
            "/decision-runs/" + case["workflow_instance_ref"] + "/replan",
            {
                "revision_id": str(body.revision_id),
                "actor_principal_id": str(ctx.owner_id),
                "note": body.note,
            },
            key,
        )
        if (
            result.get("kind") != "DECISION_REPLAN_REQUESTED"
            or result.get("instance_id") != case["workflow_instance_ref"]
            or result.get("revision_id") != case["revision"]["id"]
        ):
            raise HTTPException(502, "Replan receipt belongs to another revision")
        normalized = {k: v for k, v in result.items() if k not in {"deduped", "reconciled"}}
        await self.repository.record_workflow_task(
            ctx, identifier, body.revision_id, key, normalized, event_type="REPLAN_REQUESTED"
        )
        return await self.workflow_state(ctx, identifier)

    async def verify_handoff(self, ctx, identifier, body, key):
        case = await self.get(ctx, identifier)
        if (
            self.workflow is None
            or not case["workflow_instance_ref"]
            or case["revision"]["id"] != str(body.revision_id)
        ):
            raise HTTPException(409, "No matching submitted revision to verify")
        result = await self.workflow.request(
            "/decision-runs/" + case["workflow_instance_ref"] + "/verify-handoff",
            {"revision_id": str(body.revision_id), "actor_principal_id": str(ctx.owner_id)},
            key,
        )
        await self.repository.complete_handoff(ctx, identifier, body.revision_id, result)
        return await self.workflow_state(ctx, identifier)

    async def complete_task(self, ctx, identifier, task_id, body, key):
        case = await self.get(ctx, identifier)
        if case["revision"]["id"] != str(body.revision_id) or not case["workflow_instance_ref"]:
            raise HTTPException(409, "This task does not belong to the current submitted revision")
        condition_status = "VALID"
        try:
            proof = await self.checked(ctx, case)
        except HTTPException as error:
            if body.outcome not in {"changes_required", "rejected"} or error.status_code not in {
                409,
                422,
            }:
                raise
            # Returning/rejecting an obsolete plan does not assert it is valid.
            baseline = case["revision"]["payload"].get("validation_baseline")
            if not baseline:
                raise
            proof = {**baseline, "checked_at": datetime.now(UTC).isoformat()}
            condition_status = "STALE"
        roles = {p["role"] for p in case["participants"] if p["principal_id"] == str(ctx.owner_id)}
        verifier_roles = roles.intersection({"warehouse", "logistics"})
        if body.outcome == "confirmed" and not verifier_roles:
            raise HTTPException(403, "Only an assigned verifier can confirm")
        if body.outcome == "confirmed" and (
            not verifier_roles or not all(proof[role + "_confirmable"] for role in verifier_roles)
        ):
            raise HTTPException(409, "The relevant source conditions are not yet confirmed")
        if body.outcome == "approved" and not (
            proof["warehouse_confirmable"] and proof["logistics_confirmable"]
        ):
            raise HTTPException(409, "Approval requires known Warehouse and Logistics conditions")
        result = await self.workflow.request(
            "/decision-runs/"
            + case["workflow_instance_ref"]
            + "/tasks/"
            + str(task_id)
            + "/complete",
            {
                "actor_principal_id": str(ctx.owner_id),
                "outcome": body.outcome,
                "note": body.note,
                "check": {
                    "revision_id": str(body.revision_id),
                    "input_sha256": proof["input_sha256"],
                    "checked_at": proof["checked_at"],
                    "condition_status": condition_status,
                },
            },
            key,
        )
        receipt = {
            name: result[name]
            for name in ("task_id", "actor_principal_id", "outcome", "note", "role")
        }
        await self.repository.record_workflow_task(ctx, identifier, body.revision_id, key, receipt)
        return await self.workflow_state(ctx, identifier)

    async def create(self, ctx, body, key):
        warehouse, payload = await self.snapshot(
            ctx, body.simulation_run_id, body.order_id, body.handoff_plan
        )
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
        coordinator = value["coordinator_id"] == str(
            ctx.owner_id
        ) and "coordinator" in self.policy.roles(ctx, value["warehouse_id"])
        value["actions"] = {
            "can_revise": coordinator and value["status"] in {"DRAFT", "NEEDS_REVISION"},
            "can_submit": coordinator
            and self.workflow is not None
            and (
                value["status"] == "DRAFT"
                or value["status"] == "VALIDATING"
                and not value["workflow_instance_ref"]
            ),
        }
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
        handoff = body.handoff_plan
        if "handoff_plan" not in body.model_fields_set:
            previous = case["revision"]["payload"].get("handoff_plan")
            handoff = HandoffPlan.model_validate(previous) if previous else None
        warehouse, payload = await self.snapshot(
            ctx, body.simulation_run_id, UUID(case["order_id"]), handoff
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
        case = await self.get(ctx, identifier)
        # Older events can reference a source whose permission has since changed.
        rows = await self.repository.timeline(ctx, identifier)
        producers = {
            row["payload"]["producer"] for row in rows if row["event_type"] == "SOURCE_OBSERVED"
        }
        if producers:
            if self.event_reader is None:
                raise HTTPException(503, "Source event permissions cannot currently be checked")
            for producer in sorted(producers):
                await self.event_reader(
                    ctx,
                    producer,
                    {
                        "org_id": str(ctx.org_id),
                        "warehouse_id": case["warehouse_id"],
                        "since": datetime.now(UTC).isoformat(),
                        "limit": 1,
                    },
                )
        revision_ids = {UUID(row["revision_id"]) for row in rows if row["revision_id"]}
        referenced = await self.repository.revisions_for(ctx, identifier, revision_ids)
        for row in referenced:
            await self.permitted_payload(ctx, row["payload"])
        return {"items": rows[:500], "has_more": len(rows) > 500}
