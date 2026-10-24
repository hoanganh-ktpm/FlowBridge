"""Core composition for Workflow ports; authorization and persistence reuse Core boundaries."""

import json
import math
import re
from dataclasses import asdict
from uuid import UUID, uuid5

from pydantic import ValidationError

from app.core.applications.manifest import fingerprint
from app.core.data_products.querying import QuerySelection
from app.core.data_products.schemas import DataContractDocument
from app.core.db.commands import CommandExecutor, timestamp
from app.core.db.ports import CommandReceipt, StoredResource
from app.core.governance.policy import CoreAdminResolver, resolve_principal
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.workflow.actions import ActionDefinition, ActionRegistry
from app.platform.workflow.gateway import DataAccessPort

WORKFLOW_NAMESPACE = UUID("b93451a2-7b97-469d-aef5-8c01e60e90d8")


class CoreWorkflowPolicy(CoreAdminResolver):
    async def require_actor(self, actor):
        async with self._store.transaction(read_only=True) as transaction:
            await resolve_principal(transaction, actor)


class CoreWorkflowRuns:
    def __init__(self, store, engine, data, execution_identity, task_assignee_subjects=frozenset()):
        self.store, self.engine, self.data = store, engine, data
        self.execution_identity = execution_identity
        self.inbox = CoreEventInbox(store)
        self.task_assignee_subjects = task_assignee_subjects

    async def authorize(self, actor, instance_id):
        async with self.store.transaction(read_only=True) as transaction:
            await resolve_principal(transaction, actor)
            records = await transaction.find("workflow_event", status="COMPLETED")
        matches = [
            record.public["result_json"]
            for record in records
            if record.public["result_json"].get("kind") == "RUN_START"
            and record.public["result_json"].get("id") == instance_id
        ]
        if len(matches) != 1 or (matches[0]["owner_issuer"], matches[0]["owner_subject"]) != (
            actor.issuer,
            actor.subject,
        ):
            raise CoreError(ErrorCode.FORBIDDEN)

    async def start(self, actor, key, business_key, variables, idempotency_key, request_id):
        execution = await self.execution_identity()
        if (execution.issuer, execution.subject, execution.account_type, execution.client_id) != (
            actor.issuer,
            actor.subject,
            actor.account_type,
            actor.client_id,
        ):
            raise CoreError(ErrorCode.FORBIDDEN)
        if variables.get("assignee", actor.subject) not in {
            actor.subject,
            *self.task_assignee_subjects,
        }:
            raise CoreError(ErrorCode.FORBIDDEN)
        payload = variables.get("data_request")
        if not isinstance(payload, dict) or not isinstance(variables.get("custom_request"), dict):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        from app.platform.workflow.actions import ActionRegistry

        ActionRegistry().resolve("data.get_record").validate_input(payload)
        await self.data.execute("data.get_record", payload, actor, request_id)
        content_hash = fingerprint(
            {"key": key, "business_key": business_key, "variables": variables}
        )
        dispatch_key = fingerprint(["RUN_START", actor.issuer, actor.subject, key, idempotency_key])
        engine_variables = {
            "assignee": variables.get("assignee", actor.subject),
            "order_id": business_key,
            "inventory_request": json.dumps(payload),
            "custom_request": json.dumps(variables["custom_request"]),
            "internal_start_ref": dispatch_key,
        }
        requests = variables.get("action_requests", {})
        if not isinstance(requests, dict) or len(requests) > 10:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        for name, request in requests.items():
            if (
                not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name)
                or name in engine_variables
                or not isinstance(request, dict)
            ):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            engine_variables[name] = json.dumps(request)
        if "required_quantity" in variables:
            quantity = variables["required_quantity"]
            if (
                type(quantity) not in (int, float)
                or not math.isfinite(quantity)
                or not 0 < quantity <= 1e9
            ):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            engine_variables["required_quantity"] = quantity

        async def dispatch():
            instance = await self.engine.start_process(key, business_key, engine_variables)
            return {
                "kind": "RUN_START",
                "id": instance["id"],
                "businessKey": business_key,
                "workflow_key": key,
                "owner_issuer": actor.issuer,
                "owner_subject": actor.subject,
            }

        return await self.inbox.once(dispatch_key, content_hash, dispatch)


class CoreDataAccess(DataAccessPort):
    def __init__(self, queries):
        self.queries = queries

    async def execute(self, action_id, payload, actor, request_id):
        if action_id not in {"data.get_record", "data.query"}:
            raise CoreError(ErrorCode.FORBIDDEN)
        async with self.queries.core.store.transaction(read_only=True) as transaction:
            await resolve_principal(transaction, actor)
            products = await transaction.find("product", product_key=payload["product_key"])
            if len(products) != 1:
                raise CoreError(ErrorCode.NOT_FOUND)
            product = products[0].public
            contracts = await transaction.find(
                "contract",
                data_product_id=product["id"],
                version=payload["contract_version"],
                status="PUBLISHED",
            )
            if len(contracts) != 1:
                raise CoreError(ErrorCode.NOT_FOUND)
            contract = contracts[0].public
            document = DataContractDocument.model_validate(contract["contract_json"])
        filters = payload.get("filters", {})
        if action_id == "data.get_record" and (
            not document.business_key or set(filters) != set(document.business_key)
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if any(isinstance(value, (dict, list)) or value is None for value in filters.values()):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        try:
            selection = QuerySelection(
                select=payload.get("columns", [item.name for item in document.fields]),
                contract_version_id=contract["id"],
                where={name: {"_eq": value} for name, value in filters.items()},
                limit=2 if action_id == "data.get_record" else payload.get("limit", 20),
            )
        except ValidationError:
            raise CoreError(ErrorCode.VALIDATION_ERROR) from None
        result = await self.queries.run(actor, UUID(product["id"]), selection, request_id)
        if action_id == "data.query":
            return result
        rows = result["rows"]
        if len(rows) != 1:
            raise CoreError(ErrorCode.NOT_FOUND if not rows else ErrorCode.VERSION_CONFLICT)
        return rows[0]


class CoreActionRegistry(ActionRegistry):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.durable = store.durable
        self.commands = CommandExecutor(store)

    async def snapshot(self, transaction):
        registry = ActionRegistry()
        rows = await transaction.find("workflow_action")
        for row in sorted(rows, key=lambda item: item.public["version"]):
            registry.register(ActionDefinition(**row.public["definition_json"]))
        return registry

    async def register_async(self, definition, *, actor=None, key=None, request_id=None):
        if actor is None or not key or not request_id:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        values = asdict(definition)

        async def operation(context):
            registry = await self.snapshot(context.transaction)
            registry.register(definition)
            applications = await context.transaction.find(
                "application", app_key=definition.owner_application, status="ACTIVE"
            )
            if len(applications) != 1:
                raise CoreError(ErrorCode.NOT_FOUND)
            resource = context.resource(
                {
                    "action_id": definition.action_id,
                    "version": definition.version,
                    "definition_json": values,
                }
            )
            await context.transaction.insert("workflow_action", resource)
            await context.transaction.audit(
                context.principal.principal_id,
                request_id,
                "workflow.action.register",
                UUID(resource.public["id"]),
                resource_type="workflow_action",
            )
            return CommandReceipt(
                "", 201, {"action_id": definition.action_id, "version": definition.version}
            )

        await self.commands.execute(
            actor, "POST", "/api/v1/workflows/actions", key, values, request_id, operation
        )

    async def resolve_async(self, action_id, version=None, scope=None):
        async with self.store.transaction(read_only=True) as transaction:
            registry = await self.snapshot(transaction)
            definition = registry.resolve(action_id, version, scope)
            if definition.implementation_type == "HTTP":
                applications = await transaction.find(
                    "application", app_key=definition.owner_application, status="ACTIVE"
                )
                if len(applications) != 1:
                    raise CoreError(ErrorCode.FORBIDDEN)
                department = await transaction.get(
                    "department", UUID(applications[0].public["department_id"])
                )
                if department.public["status"] != "ACTIVE":
                    raise CoreError(ErrorCode.FORBIDDEN)
        return definition


class CoreEventInbox:
    def __init__(self, store):
        self.store = store
        self.durable = store.durable

    async def finish(self, resource_id, status, result):
        async with self.store.transaction() as transaction:
            resource = await transaction.get("workflow_event", resource_id)
            await transaction.replace(
                "workflow_event",
                StoredResource(
                    {
                        **resource.public,
                        "status": status,
                        "result_json": result,
                        "updated_at": timestamp(await transaction.now()),
                        "revision": resource.public["revision"] + 1,
                    },
                    {},
                ),
            )

    async def once(self, key, payload_hash, dispatch):
        resource_id = uuid5(WORKFLOW_NAMESPACE, "event:" + key)
        async with self.store.permission_lock(resource_id, WORKFLOW_NAMESPACE):
            async with self.store.transaction() as transaction:
                try:
                    previous = await transaction.get("workflow_event", resource_id)
                except CoreError as error:
                    if error.code != ErrorCode.NOT_FOUND:
                        raise
                    previous = None
                if previous is not None:
                    if previous.public["payload_hash"] != payload_hash:
                        raise CoreError(ErrorCode.IDEMPOTENCY_CONFLICT)
                    if previous.public["status"] != "COMPLETED":
                        raise CoreError(ErrorCode.INVALID_STATE)
                    return {**previous.public["result_json"], "deduped": True}
                now = timestamp(await transaction.now())
                await transaction.insert(
                    "workflow_event",
                    StoredResource(
                        {
                            "id": str(resource_id),
                            "payload_hash": payload_hash,
                            "status": "DISPATCHING",
                            "result_json": {},
                            "revision": 1,
                            "created_at": now,
                            "updated_at": now,
                        },
                        {},
                    ),
                )
            try:
                result = await dispatch()
            except Exception:
                await self.finish(resource_id, "UNCERTAIN", {})
                raise
            await self.finish(resource_id, "COMPLETED", result)
            return result
