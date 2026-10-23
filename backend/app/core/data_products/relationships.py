from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.core.data_products.schemas import DataContractDocument
from app.core.data_products.validation import require_physical_contract
from app.core.data_sources.discovery import canonical_type
from app.core.db.commands import require_revision, require_state, uuid
from app.core.db.ports import CommandReceipt
from app.core.governance.policy import resolve_principal
from app.core.governance.schemas import Identifier, RevisionCommand
from app.core.governance.service import current_product, enqueue
from app.core.hasura_provisioner.schemas import DTO, TableRef
from app.platform.http.errors import CoreError, ErrorCode


class RelationshipDefinition(DTO):
    name: Identifier
    source_product_id: UUID
    target_product_id: UUID
    relationship_type: Literal["object", "array"]
    field_mapping: dict[Identifier, Identifier] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_mapping(self):
        if len(set(self.field_mapping.values())) != len(self.field_mapping):
            raise ValueError("Composite mappings must be one-to-one")
        return self


class RelationshipPatch(RevisionCommand):
    field_mapping: dict[Identifier, Identifier] = Field(min_length=1, max_length=20)


def table_ref(product):
    return TableRef(schema=product["schema_name"], name=product["object_name"])


class RelationshipRegistry:
    def __init__(self, core, metadata):
        self.core, self.metadata = core, metadata

    async def validate(self, transaction, definition, snapshots=None):
        snapshots = {} if snapshots is None else snapshots
        endpoints = []
        for side in ("source", "target"):
            product, source, contract = await current_product(
                transaction, definition[side + "_product_id"], definition.get(side + "_contract_id")
            )
            if source.public["id"] not in snapshots:
                snapshots[source.public["id"]] = await self.core.discovery.snapshot(source)
            snapshot = snapshots[source.public["id"]]
            await require_physical_contract(
                product.public,
                DataContractDocument.model_validate(contract.public["contract_json"]),
                snapshot,
                self.core.evidence,
            )
            physical = snapshot.find(table_ref(product.public))
            require_state(physical is not None)
            endpoints.append((product, source, contract, physical))
        source_product, _, source_contract, source_table = endpoints[0]
        _, _, target_contract, target_table = endpoints[1]
        source_fields = DataContractDocument.model_validate(source_contract.public["contract_json"])
        target_fields = DataContractDocument.model_validate(target_contract.public["contract_json"])
        mapping = definition["field_mapping"]
        if (
            len(set(mapping.values())) != len(mapping)
            or not set(mapping) <= {field.column for field in source_fields.fields}
            or not set(mapping.values()) <= {field.column for field in target_fields.fields}
            or definition["name"] in {field.name for field in source_fields.fields}
            or definition["name"].endswith("_aggregate")
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        local = {column.name: column for column in source_table.columns}
        remote = {column.name: column for column in target_table.columns}
        if any(
            canonical_type(local[left].pg_type) != canonical_type(remote[right].pg_type)
            for left, right in mapping.items()
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        unique_table, keys = (
            (target_table, set(mapping.values()))
            if definition["relationship_type"] == "object"
            else (source_table, set(mapping))
        )
        if not any(
            key and set(key) <= keys
            for key in (unique_table.primary_key, *unique_table.unique_constraints)
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if not source_product.public["active_contract_id"]:
            raise CoreError(ErrorCode.INVALID_STATE)
        return endpoints

    async def command(self, operation, identity, method, path, key, payload, correlation_id, **ids):
        async def execute(context):
            result = await getattr(self, "_" + operation)(context, payload, **ids)
            await context.transaction.audit(
                context.principal.principal_id,
                correlation_id,
                "relationship." + operation,
                uuid(ids.get("relationship_id") or result.body.get("id") or result.body["job_id"]),
                resource_type="relationship",
            )
            return result

        return await self.core.commands.execute(
            identity, method, path, key, payload, correlation_id, execute
        )

    async def _create(self, context, body):
        endpoints = await self.validate(context.transaction, body)
        require_state(
            not await context.transaction.find(
                "relationship", source_product_id=body["source_product_id"], name=body["name"]
            )
        )
        record = context.resource(
            {
                **body,
                "source_contract_id": endpoints[0][2].public["id"],
                "target_contract_id": endpoints[1][2].public["id"],
                "status": "DRAFT",
                "applied_revision": 0,
                "last_error_code": None,
            }
        )
        await context.transaction.insert("relationship", record)
        return CommandReceipt("", 201, record.public)

    async def _patch(self, context, body, relationship_id):
        record = await context.transaction.get("relationship", relationship_id)
        require_revision(record, body["expected_revision"])
        require_state(
            record.public["status"] == "DRAFT"
            or (
                record.public["status"] == "DISABLED"
                and record.public["applied_revision"] == record.public["revision"]
            )
        )
        candidate = {**record.public, "field_mapping": body["field_mapping"]}
        candidate.pop("source_contract_id")
        candidate.pop("target_contract_id")
        endpoints = await self.validate(context.transaction, candidate)
        record = context.update(
            record,
            field_mapping=body["field_mapping"],
            status="DRAFT",
            source_contract_id=endpoints[0][2].public["id"],
            target_contract_id=endpoints[1][2].public["id"],
            last_error_code=None,
        )
        await context.transaction.replace("relationship", record)
        return CommandReceipt("", 200, record.public)

    async def _publish(self, context, body, relationship_id):
        record = await context.transaction.get("relationship", relationship_id)
        require_revision(record, body["expected_revision"])
        require_state(record.public["status"] in {"DRAFT", "ERROR", "ACTIVE"})
        await self.validate(context.transaction, record.public)
        record = context.update(record, status="PROVISIONING", last_error_code=None)
        await context.transaction.replace("relationship", record)
        job = await enqueue(context, "PUBLISH_RELATIONSHIP", record)
        return CommandReceipt("", 202, job, job["status_url"])

    async def _disable(self, context, body, relationship_id):
        record = await context.transaction.get("relationship", relationship_id)
        require_revision(record, body["expected_revision"])
        require_state(record.public["status"] != "DISABLING")
        record = context.update(record, status="DISABLING", last_error_code=None)
        await context.transaction.replace("relationship", record)
        job = await enqueue(context, "DISABLE_RELATIONSHIP", record)
        return CommandReceipt("", 202, job, job["status_url"])

    async def get(self, identity, relationship_id):
        async with self.core.store.transaction(read_only=True) as transaction:
            (await resolve_principal(transaction, identity)).require_admin()
            return (await transaction.get("relationship", relationship_id)).public

    async def list(self, identity, source_product_id):
        async with self.core.store.transaction(read_only=True) as transaction:
            (await resolve_principal(transaction, identity)).require_admin()
            return {
                "items": [
                    record.public
                    for record in await transaction.find(
                        "relationship", source_product_id=str(source_product_id)
                    )
                ]
            }
