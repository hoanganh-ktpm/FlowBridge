# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit trusted Workspace execution port; Flowable remains workflow authority."""

from datetime import UTC, datetime
from uuid import UUID

from app.core.applications.manifest import fingerprint
from app.core.governance.policy import resolve_principal
from app.core.workflow import CoreEventInbox
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.workflow.decision_schemas import Assignment

KEY = "supply_handover_exception"
TASK_ROLES = {
    "verifyWarehouse": "warehouse",
    "verifyLogistics": "logistics",
    "managerReview": "manager",
    "warehouseReminder": "coordinator",
    "logisticsReminder": "coordinator",
    "managerReminder": "coordinator",
}
OUTCOMES = {
    "warehouse": {"confirmed", "changes_required"},
    "logistics": {"confirmed", "changes_required"},
    "manager": {"approved", "changes_required", "rejected"},
    "coordinator": {"acknowledged"},
}


class CoreDecisionRuns:
    def __init__(self, store, engine, data, execution_identity, bindings):
        self.store, self.engine, self.data = store, engine, data
        self.execution_identity = execution_identity
        self.bindings = {
            str(UUID(key)): Assignment.model_validate(value) for key, value in bindings.items()
        }
        self.inbox = CoreEventInbox(store)

    async def trusted(self, actor):
        execution = await self.execution_identity()
        if actor.account_type != "service" or (
            actor.issuer,
            actor.subject,
            actor.account_type,
            actor.client_id,
        ) != (execution.issuer, execution.subject, execution.account_type, execution.client_id):
            raise CoreError(ErrorCode.FORBIDDEN)
        async with self.store.transaction(read_only=True) as tx:
            await resolve_principal(tx, actor)

    async def assigned(self, principal_id, role, org_id, warehouse_id):
        binding = self.bindings.get(str(principal_id))
        if (
            not binding
            or binding.org_id != UUID(str(org_id))
            or UUID(str(warehouse_id)) not in binding.warehouse_ids
            or role not in binding.roles
        ):
            raise CoreError(ErrorCode.FORBIDDEN)
        async with self.store.transaction(read_only=True) as tx:
            identity = await tx.identity_for_principal(UUID(str(principal_id)))
            await resolve_principal(tx, identity)
        return identity

    @staticmethod
    def fresh(check, record):
        if (
            str(check.revision_id) != str(record["revision_id"])
            or check.input_sha256 != record["input_sha256"]
        ):
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        age = (datetime.now(UTC) - check.checked_at).total_seconds()
        if not 0 <= age <= 60:
            raise CoreError(ErrorCode.VERSION_CONFLICT)

    async def start(self, actor, body, request_id):
        await self.trusted(actor)
        roles = body.participants
        if (
            set(roles) != {"coordinator", "warehouse", "logistics", "manager"}
            or roles["manager"] in {roles["coordinator"], roles["warehouse"], roles["logistics"]}
            or roles["coordinator"] in {roles["warehouse"], roles["logistics"]}
        ):
            raise CoreError(ErrorCode.FORBIDDEN)
        identities = {
            role: await self.assigned(principal, role, body.org_id, body.warehouse_id)
            for role, principal in roles.items()
        }
        if any(identity.issuer != actor.issuer for identity in identities.values()):
            raise CoreError(ErrorCode.FORBIDDEN)
        record = {
            "kind": "DECISION_RUN",
            "case_id": str(body.case_id),
            "revision_id": str(body.revision_id),
            "revision_number": body.revision_number,
            "org_id": str(body.org_id),
            "warehouse_id": str(body.warehouse_id),
            "order_id": str(body.order_id),
            "input_sha256": body.check.input_sha256,
            "participants": {role: str(value) for role, value in roles.items()},
            "owner_issuer": actor.issuer,
            "owner_subject": actor.subject,
        }
        self.fresh(body.check, record)
        if body.check.condition_status != "VALID":
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        source = await self.data.decision_order(
            actor,
            body.order_source_id,
            body.order_contract_id,
            body.org_id,
            body.warehouse_id,
            body.order_id,
            request_id,
        )
        if any(
            str(source.get(column)) != record[column]
            for column in ("org_id", "warehouse_id", "order_id")
        ):
            raise CoreError(ErrorCode.FORBIDDEN)
        dispatch_key = fingerprint(
            [
                "DECISION_RUN",
                actor.issuer,
                actor.subject,
                str(body.org_id),
                str(body.case_id),
                str(body.revision_id),
            ]
        )
        # Timestamp is a freshness guard, not command identity.
        digest = fingerprint(
            {
                **record,
                "order_source_id": str(body.order_source_id),
                "order_contract_id": str(body.order_contract_id),
            }
        )
        variables = {
            **{role + "_assignee": identity.subject for role, identity in identities.items()},
            "case_id": str(body.case_id),
            "revision_id": str(body.revision_id),
            "internal_start_ref": dispatch_key,
        }

        async def dispatch():
            instance = await self.engine.start_process(KEY, str(body.revision_id), variables)
            return {**record, "id": instance["id"]}

        async def recover():
            found = await self.engine.find_process(KEY, str(body.revision_id), dispatch_key)
            return {**record, "id": found[0]["id"]} if len(found) == 1 else None

        return await self.inbox.once(dispatch_key, digest, dispatch, recover=recover)

    async def record(self, actor, instance_id):
        await self.trusted(actor)
        async with self.store.transaction(read_only=True) as tx:
            records = await tx.find("workflow_event", status="COMPLETED")
        matches = [
            row.public["result_json"]
            for row in records
            if row.public["result_json"].get("kind") == "DECISION_RUN"
            and row.public["result_json"].get("id") == instance_id
            and row.public["result_json"].get("owner_issuer") == actor.issuer
            and row.public["result_json"].get("owner_subject") == actor.subject
        ]
        if len(matches) != 1:
            raise CoreError(ErrorCode.FORBIDDEN)
        return matches[0]

    async def state(self, actor, instance_id, principal_id):
        record = await self.record(actor, instance_id)
        roles = [
            role
            for role, identifier in record["participants"].items()
            if identifier == str(principal_id)
        ]
        if not roles:
            raise CoreError(ErrorCode.FORBIDDEN)
        for role in roles:
            await self.assigned(principal_id, role, record["org_id"], record["warehouse_id"])
        instance = await self.engine.get_instance(instance_id)
        tasks = await self.engine.list_tasks(process_instance_id=instance_id)
        status = (
            "NEEDS_REVISION"
            if instance.get("endActivityId") == "revisionRequired"
            else "STOPPED"
            if instance.get("endActivityId") == "stopped"
            else "COMPLETED"
            if instance.get("endActivityId") == "handedOver"
            else "PENDING_APPROVAL"
            if any(t["taskDefinitionKey"] == "managerReview" for t in tasks)
            else "VALIDATING"
            if any(t["taskDefinitionKey"] in {"verifyWarehouse", "verifyLogistics"} for t in tasks)
            else "APPROVED"
            if await self.engine.awaits_message(instance_id, "decision.carrier_handed_over")
            else "UNKNOWN"
        )
        if status == "UNKNOWN":
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return {
            "instance_id": instance_id,
            "revision_id": record["revision_id"],
            "status": status,
            "tasks": [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "task_key": t["taskDefinitionKey"],
                    "role": TASK_ROLES.get(t["taskDefinitionKey"]),
                    "mine": TASK_ROLES.get(t["taskDefinitionKey"]) in roles,
                }
                for t in tasks
            ],
        }

    async def complete(self, actor, instance_id, task_id, body, key):
        record = await self.record(actor, instance_id)
        self.fresh(body.check, record)
        if body.outcome in {"confirmed", "approved"} and body.check.condition_status != "VALID":
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        completed = False
        try:
            task = await self.engine.get_task(task_id)
        except CoreError as error:
            if error.code != ErrorCode.NOT_FOUND:
                raise
            from app.platform.workflow.flowable import engine_id

            task = await self.engine.request(
                "GET", "/history/historic-task-instances/" + engine_id(task_id)
            )
            completed = task.get("endTime") is not None
            if not completed:
                raise CoreError(ErrorCode.INVALID_STATE) from None
        role = TASK_ROLES.get(task.get("taskDefinitionKey"))
        if (
            task.get("processInstanceId") != instance_id
            or role is None
            or record["participants"][role] != str(body.actor_principal_id)
            or body.outcome not in OUTCOMES[role]
        ):
            raise CoreError(ErrorCode.FORBIDDEN)
        identity = await self.assigned(
            body.actor_principal_id, role, record["org_id"], record["warehouse_id"]
        )
        if task.get("assignee") != identity.subject:
            raise CoreError(ErrorCode.FORBIDDEN)
        variables = {} if role == "coordinator" else {role + "_outcome": body.outcome}
        dispatch_key = fingerprint(["DECISION_TASK", instance_id, task_id, key])
        digest = fingerprint(
            [
                str(body.actor_principal_id),
                body.outcome,
                str(body.check.revision_id),
                body.check.input_sha256,
                body.note,
            ]
        )

        async def dispatch():
            if completed:
                raise CoreError(ErrorCode.INVALID_STATE)
            await self.engine.complete_task(task_id, variables)
            return {
                "task_id": task_id,
                "actor_principal_id": str(body.actor_principal_id),
                "outcome": body.outcome,
                "note": body.note,
                "role": role,
            }

        async def recover():
            if completed:
                return {
                    "task_id": task_id,
                    "actor_principal_id": str(body.actor_principal_id),
                    "outcome": body.outcome,
                    "note": body.note,
                    "role": role,
                }
            return None

        return await self.inbox.once(dispatch_key, digest, dispatch, recover=recover)
