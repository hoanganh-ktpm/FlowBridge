from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.applications.manifest import fingerprint, source_name
from app.core.data_products.reading import ReadTarget, TrustedHasuraContext, validate_rows
from app.core.data_products.schemas import DataContractDocument
from app.core.data_products.validation import require_physical_contract
from app.core.data_sources.service import SourceDiscoveryService, source_record
from app.core.db.commands import (
    CommandContext,
    CommandExecutor,
    require_revision,
    require_state,
    timestamp,
    uuid,
)
from app.core.db.ports import CommandReceipt, CoreStore, CoreTransaction, StoredResource
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity

from .policy import resolve_principal
from .schemas import public_projection


def instant(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


async def active_department(transaction: CoreTransaction, department_id: UUID) -> None:
    require_state((await transaction.get("department", department_id)).public["status"] == "ACTIVE")


async def source_affiliation(
    transaction: CoreTransaction, source: StoredResource
) -> StoredResource:
    application = await transaction.get("application", uuid(source.public["application_id"]))
    require_state(application.public["status"] == "ACTIVE")
    await active_department(transaction, uuid(application.public["department_id"]))
    return application


async def current_product(transaction, product_id, contract_id=None):
    product = await transaction.get("product", uuid(product_id))
    source = await transaction.get("source", uuid(product.public["data_source_id"]))
    require_state(product.public["status"] == "ACTIVE" and source.public["status"] == "CONNECTED")
    await source_affiliation(transaction, source)
    selected = contract_id or product.public["active_contract_id"]
    require_state(selected is not None and str(selected) == product.public["active_contract_id"])
    contract = await transaction.get("contract", uuid(selected))
    require_state(
        contract.public["status"] == "PUBLISHED"
        and contract.public["data_product_id"] == product.public["id"]
    )
    return product, source, contract


async def visible_product(transaction, product_id, contract_id=None):
    try:
        return await current_product(transaction, product_id, contract_id)
    except CoreError as error:
        if error.code == ErrorCode.INVALID_STATE:
            raise CoreError(ErrorCode.NOT_FOUND) from None
        raise


async def enqueue(context: CommandContext, kind: str, target: StoredResource) -> dict:
    fields = {
        "data_source_id": None,
        "data_product_id": None,
        "access_grant_id": None,
        "relationship_id": None,
    }
    field = (
        "relationship_id"
        if kind in {"PUBLISH_RELATIONSHIP", "DISABLE_RELATIONSHIP"}
        else "data_source_id"
        if kind == "CONNECT_SOURCE"
        else "access_grant_id"
        if kind in {"APPLY_GRANT", "REVOKE_GRANT"}
        else "data_product_id"
    )
    fields[field] = target.public["id"]
    revision = target.public.get("desired_revision", target.public.get("revision"))
    existing = await context.transaction.find(
        "job", job_type=kind, **{field: target.public["id"]}, target_revision=revision
    )
    if existing:
        job = existing[0].public
    else:
        job = {
            "id": str(uuid4()),
            "job_type": kind,
            **fields,
            "target_revision": revision,
            "status": "QUEUED",
            "attempt_count": 0,
            "max_attempts": 5,
            "next_attempt_at": timestamp(context.now),
            "last_error_code": None,
            "created_at": timestamp(context.now),
            "updated_at": timestamp(context.now),
        }
        await context.transaction.insert(
            "job",
            StoredResource(
                job,
                {
                    "idempotency_key": f"{kind}:{target.public['id']}:{revision}",
                    "requested_by": str(context.principal.principal_id),
                    "correlation_id": context.correlation_id,
                    "locked_by": None,
                    "lease_expires_at": None,
                },
            ),
        )
        await context.transaction.audit(
            context.principal.principal_id,
            context.correlation_id,
            "publication.enqueued",
            uuid(job["id"]),
            resource_type="job",
            content_hash=fingerprint([kind, target.public["id"], revision]),
        )
    return {
        "job_id": job["id"],
        "status": "QUEUED",
        "status_url": "/api/v1/core/publication-jobs/" + job["id"],
    }


class DataSharingService:
    def __init__(
        self, store: CoreStore, discovery: SourceDiscoveryService, reader=None, evidence=None
    ) -> None:
        self.store = store
        self.discovery = discovery
        self.reader = reader
        self.evidence = evidence
        self.commands = CommandExecutor(store)

    async def command(self, operation, identity, method, path, key, payload, correlation_id, **ids):
        async def execute(context):
            result = await getattr(self, "_" + operation)(context, payload, **ids)
            target = result.body.get("id") or result.body.get("job_id")
            if target is None and "grant" in result.body:
                target = result.body["grant"]["id"]
            await context.transaction.audit(
                context.principal.principal_id,
                correlation_id,
                operation,
                uuid(target),
                resource_type=(
                    "job"
                    if "job_id" in result.body
                    else "grant"
                    if "grant" in result.body
                    else "application"
                    if "application" in operation
                    else "source"
                    if "source" in operation
                    else "product"
                    if "product" in operation
                    else "contract"
                    if "contract" in operation
                    else "request"
                ),
                content_hash=fingerprint(payload),
            )
            return result

        return await self.commands.execute(
            identity, method, path, key, payload, correlation_id, execute
        )

    async def _create_application(self, context, body):
        await active_department(context.transaction, uuid(body["department_id"]))
        require_state(not await context.transaction.find("application", app_key=body["app_key"]))
        application = context.resource(
            {"description": None, "business_api_base_url": None, **body, "status": "DRAFT"}
        )
        await context.transaction.insert("application", application)
        return CommandReceipt("", 201, application.public)

    async def _patch_application(self, context, body, application_id):
        application = await context.transaction.get("application", application_id)
        require_revision(application, body["expected_revision"])
        changes = {key: value for key, value in body.items() if key != "expected_revision"}
        if changes.get("status") == "ACTIVE":
            await active_department(context.transaction, uuid(application.public["department_id"]))
        if "business_api_base_url" in changes or changes.get("status") == "DRAFT":
            require_state(not application.private.get("active_business_operations"))
        application = context.update(application, **changes)
        await context.transaction.replace("application", application)
        return CommandReceipt("", 200, application.public)

    async def _create_source(self, context, body):
        application = await context.transaction.get("application", uuid(body["application_id"]))
        require_state(application.public["status"] != "DISABLED")
        await active_department(context.transaction, uuid(application.public["department_id"]))
        prefix = application.public["app_key"] + "."
        if not body["source_key"].startswith(prefix):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        require_state(not await context.transaction.find("source", source_key=body["source_key"]))
        source = context.resource(
            {
                "application_id": body["application_id"],
                "source_key": body["source_key"],
                "db_type": "POSTGRESQL",
                "hasura_source_name": source_name(
                    application.public["app_key"], body["source_key"][len(prefix) :]
                ),
                "status": "DRAFT",
                "last_checked_at": None,
            },
            private={
                "ssl_mode": "verify-full",
                **{
                    key: value
                    for key, value in body.items()
                    if key not in {"application_id", "source_key", "db_type"}
                },
            },
        )
        await self.discovery.policy.check(source_record(source), source.public["source_key"])
        await context.transaction.insert("source", source)
        return CommandReceipt("", 201, source.public)

    async def _connect_source(self, context, body, data_source_id):
        source = await context.transaction.get("source", data_source_id)
        require_revision(source, body["expected_revision"])
        require_state(source.public["status"] in {"DRAFT", "FAILED", "CONNECTED"})
        await source_affiliation(context.transaction, source)
        await self.discovery.policy.check(source_record(source), source.public["source_key"])
        source = context.update(source, status="VALIDATING")
        await context.transaction.replace("source", source)
        job = await enqueue(context, "CONNECT_SOURCE", source)
        return CommandReceipt("", 202, job, job["status_url"])

    async def _create_product(self, context, body):
        source = await context.transaction.get("source", uuid(body["data_source_id"]))
        require_state(source.public["status"] != "DISABLED")
        application = await context.transaction.get(
            "application", uuid(source.public["application_id"])
        )
        require_state(application.public["status"] != "DISABLED")
        await active_department(context.transaction, uuid(application.public["department_id"]))
        if not body["product_key"].startswith(application.public["app_key"] + "."):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        require_state(
            not await context.transaction.find("product", product_key=body["product_key"])
        )
        require_state(
            not await context.transaction.find(
                "product",
                data_source_id=body["data_source_id"],
                schema_name=body["schema_name"],
                object_name=body["object_name"],
            )
        )
        product = context.resource(
            {
                "description": None,
                **body,
                "owner_department_id": application.public["department_id"],
                "active_contract_id": None,
                "status": "DRAFT",
            }
        )
        await context.transaction.insert("product", product)
        return CommandReceipt("", 201, product.public)

    async def _create_contract(self, context, body, data_product_id):
        product = await context.transaction.get("product", data_product_id)
        require_state(product.public["status"] != "DISABLED")
        require_state(
            not await context.transaction.find(
                "contract", data_product_id=str(data_product_id), version=body["version"]
            )
        )
        contract = context.resource(
            {
                **body,
                "data_product_id": str(data_product_id),
                "status": "DRAFT",
                "published_at": None,
                "published_by": None,
            }
        )
        await context.transaction.insert("contract", contract)
        return CommandReceipt("", 201, contract.public)

    async def _patch_contract(self, context, body, contract_version_id):
        contract = await context.transaction.get("contract", contract_version_id)
        require_revision(contract, body["expected_revision"])
        require_state(contract.public["status"] == "DRAFT")
        contract = context.update(contract, contract_json=body["contract_json"])
        await context.transaction.replace("contract", contract)
        return CommandReceipt("", 200, contract.public)

    async def verify_contract(self, transaction, product, contract, *, snapshot=None):
        source = await transaction.get("source", uuid(product.public["data_source_id"]))
        require_state(source.public["status"] == "CONNECTED")
        await source_affiliation(transaction, source)
        snapshot = snapshot or await self.discovery.snapshot(source)
        await require_physical_contract(
            product.public,
            DataContractDocument.model_validate(contract.public["contract_json"]),
            snapshot,
            self.evidence,
        )

    async def _publish_contract(self, context, body, contract_version_id):
        contract = await context.transaction.get("contract", contract_version_id)
        require_revision(contract, body["expected_revision"])
        require_state(contract.public["status"] == "DRAFT")
        product = await context.transaction.get("product", uuid(contract.public["data_product_id"]))
        require_state(product.public["status"] != "DISABLED")
        await self.verify_contract(context.transaction, product, contract)
        contract = context.update(
            contract,
            status="PUBLISHED",
            published_at=timestamp(context.now),
            published_by=str(context.principal.principal_id),
        )
        await context.transaction.replace("contract", contract)
        return CommandReceipt("", 200, contract.public)

    async def _publish_product(self, context, body, data_product_id):
        product = await context.transaction.get("product", data_product_id)
        require_revision(product, body["expected_revision"])
        require_state(product.public["status"] in {"DRAFT", "READY", "ERROR", "ACTIVE"})
        contract = await context.transaction.get("contract", uuid(body["contract_version_id"]))
        require_state(
            contract.public["status"] == "PUBLISHED"
            and contract.public["data_product_id"] == str(data_product_id)
        )
        if product.public["active_contract_id"] != contract.public["id"]:
            grants = await context.transaction.find("grant", data_product_id=str(data_product_id))
            require_state(
                not any(
                    grant.public["status"] in {"PENDING_APPLY", "ACTIVE", "REVOKING", "ERROR"}
                    for grant in grants
                )
            )
        await self.verify_contract(context.transaction, product, contract)
        product = context.update(
            product, status="PROVISIONING", active_contract_id=contract.public["id"]
        )
        await context.transaction.replace("product", product)
        job = await enqueue(context, "PUBLISH_PRODUCT", product)
        return CommandReceipt("", 202, job, job["status_url"])

    async def _create_request(self, context, body):
        product, _, contract = await current_product(
            context.transaction, body["data_product_id"], body["contract_version_id"]
        )
        await active_department(context.transaction, uuid(body["consumer_department_id"]))
        columns = {field["column"] for field in contract.public["contract_json"]["fields"]}
        if not set(body["requested_scope_json"]["columns"]) <= columns:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        request = context.resource(
            {
                **body,
                "requested_by": str(context.principal.principal_id),
                "requested_at": timestamp(context.now),
                "status": "PENDING",
                "decision_note": None,
                "decided_by": None,
                "decided_at": None,
            }
        )
        await context.transaction.insert("request", request)
        return CommandReceipt("", 201, request.public)

    async def _approve_request(self, context, body, access_request_id):
        request = await context.transaction.get("request", access_request_id)
        require_revision(request, body["expected_revision"])
        require_state(request.public["status"] == "PENDING")
        await current_product(
            context.transaction,
            request.public["data_product_id"],
            request.public["contract_version_id"],
        )
        await active_department(context.transaction, uuid(request.public["consumer_department_id"]))
        if not set(body["scope_json"]["columns"]) <= set(
            request.public["requested_scope_json"]["columns"]
        ) or (body["expires_at"] and instant(body["expires_at"]) <= context.now):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        existing = await context.transaction.find(
            "grant",
            data_product_id=request.public["data_product_id"],
            consumer_department_id=request.public["consumer_department_id"],
        )
        require_state(
            not any(grant.public["status"] not in {"REVOKED", "EXPIRED"} for grant in existing)
        )
        request = context.update(
            request,
            status="APPROVED",
            decision_note=body.get("decision_note"),
            decided_by=str(context.principal.principal_id),
            decided_at=timestamp(context.now),
        )
        grant = StoredResource(
            {
                "id": str(uuid4()),
                "data_product_id": request.public["data_product_id"],
                "contract_version_id": request.public["contract_version_id"],
                "access_request_id": request.public["id"],
                "consumer_department_id": request.public["consumer_department_id"],
                "consumer_principal_id": None,
                "scope_json": body["scope_json"],
                "desired_revision": 1,
                "applied_revision": 0,
                "status": "PENDING_APPLY",
                "expires_at": body["expires_at"],
                "granted_by": str(context.principal.principal_id),
                "granted_at": timestamp(context.now),
                "revoked_by": None,
                "revoked_at": None,
                "updated_at": timestamp(context.now),
            },
            {},
        )
        await context.transaction.replace("request", request)
        await context.transaction.insert("grant", grant)
        job = await enqueue(context, "APPLY_GRANT", grant)
        return CommandReceipt(
            "",
            202,
            {"access_request": request.public, "grant": grant.public, "job": job},
            job["status_url"],
        )

    async def _revoke_grant(self, context, body, access_grant_id):
        grant = await context.transaction.get("grant", access_grant_id)
        require_revision(grant, body["expected_desired_revision"], "desired_revision")
        require_state(grant.public["status"] not in {"REVOKED", "EXPIRED", "REVOKING"})
        grant = StoredResource(
            {
                **grant.public,
                "desired_revision": grant.public["desired_revision"] + 1,
                "status": "REVOKING",
                "revoked_by": str(context.principal.principal_id),
                "revoked_at": timestamp(context.now),
                "updated_at": timestamp(context.now),
            },
            grant.private,
        )
        await context.transaction.replace("grant", grant)
        job = await enqueue(context, "REVOKE_GRANT", grant)
        return CommandReceipt("", 202, job, job["status_url"])

    async def _disable_product(self, context, body, data_product_id):
        product = await context.transaction.get("product", data_product_id)
        require_revision(product, body["expected_revision"])
        require_state(product.public["status"] != "DISABLED")
        product = context.update(product, status="DISABLED")
        await context.transaction.replace("product", product)
        for grant in await context.transaction.find("grant", data_product_id=str(data_product_id)):
            if grant.public["status"] in {"REVOKED", "EXPIRED"}:
                continue
            await context.transaction.replace(
                "grant",
                StoredResource(
                    {
                        **grant.public,
                        "status": "REVOKING",
                        "desired_revision": grant.public["desired_revision"] + 1,
                        "revoked_by": str(context.principal.principal_id),
                        "revoked_at": timestamp(context.now),
                        "updated_at": timestamp(context.now),
                    },
                    grant.private,
                ),
            )
        job = await enqueue(context, "DISABLE_PRODUCT", product)
        return CommandReceipt("", 202, job, job["status_url"])

    async def get(self, identity: VerifiedIdentity, kind: str, resource_id: UUID) -> dict[str, Any]:
        async with self.store.transaction(read_only=True) as transaction:
            principal = await resolve_principal(transaction, identity)
            if principal.platform_role != "PLATFORM_ADMIN" and kind not in {
                "product",
                "contract",
                "grant",
            }:
                raise CoreError(ErrorCode.FORBIDDEN)
            resource = await transaction.get(kind, resource_id)
            if principal.platform_role != "PLATFORM_ADMIN":
                if kind == "product":
                    await visible_product(transaction, resource_id)
                elif kind == "contract":
                    await visible_product(
                        transaction, resource.public["data_product_id"], resource_id
                    )
                elif kind == "grant":
                    if uuid(resource.public["consumer_department_id"]) not in principal.departments(
                        await transaction.now()
                    ):
                        raise CoreError(ErrorCode.NOT_FOUND)
                    department = await transaction.get(
                        "department", uuid(resource.public["consumer_department_id"])
                    )
                    if department.public["status"] != "ACTIVE":
                        raise CoreError(ErrorCode.NOT_FOUND)
                else:
                    raise CoreError(ErrorCode.FORBIDDEN)
            return public_projection(kind, resource)

    async def discover(self, identity, source_id, schema=None, names=()):
        async with self.store.transaction() as transaction:
            (await resolve_principal(transaction, identity)).require_admin()
            source = await transaction.get("source", source_id)
            require_state(source.public["status"] == "CONNECTED")
            await source_affiliation(transaction, source)
            captured = timestamp(await transaction.now())
        return self.discovery.projection(
            str(source_id), await self.discovery.snapshot(source, schema, names), captured
        )

    async def authorized_products(self, identity):
        """Discover readable contracts through existing grants, including for IT callers."""
        items = []
        async with self.store.transaction(read_only=True) as transaction:
            principal = await resolve_principal(transaction, identity)
            now = await transaction.now()
            candidates = {}
            for department_id in principal.departments(now):
                for grant in await transaction.find(
                    "grant", consumer_department_id=str(department_id), status="ACTIVE"
                ):
                    data = grant.public
                    if (
                        data["consumer_principal_id"] is not None
                        or data["desired_revision"] != data["applied_revision"]
                        or (data["expires_at"] and instant(data["expires_at"]) <= now)
                    ):
                        continue
                    try:
                        product, source, contract = await current_product(
                            transaction,
                            uuid(data["data_product_id"]),
                            uuid(data["contract_version_id"]),
                        )
                    except CoreError:
                        continue
                    document = contract.public["contract_json"]
                    fields = [
                        f
                        for f in document["fields"]
                        if f["column"] in data["scope_json"]["columns"]
                    ]
                    if not set(document["business_key"]) <= {f["name"] for f in fields}:
                        continue
                    key = (product.public["id"], contract.public["id"])
                    application = await transaction.get(
                        "application", uuid(source.public["application_id"])
                    )
                    # Do not combine columns from different grants: rows requires one grant.
                    candidate = {
                        "id": product.public["id"],
                        "name": product.public["name"],
                        "contract_version_id": contract.public["id"],
                        "fields": fields,
                        "business_key": document["business_key"],
                        "application": {
                            "id": application.public["id"],
                            "name": application.public["name"],
                            "key": application.public["app_key"],
                        },
                    }
                    if key not in candidates or len(fields) > len(candidates[key]["fields"]):
                        candidates[key] = candidate
            items = sorted(candidates.values(), key=lambda p: (p["name"], p["id"]))
        return {"items": items}

    async def read_rows(self, identity, product_id, contract_id, columns, limit, offset):
        async with self.store.transaction() as transaction:
            principal = await resolve_principal(transaction, identity)
            now = await transaction.now()
            try:
                product, source, contract = await current_product(
                    transaction, product_id, contract_id
                )
            except CoreError as error:
                if error.code == ErrorCode.INVALID_STATE:
                    raise CoreError(ErrorCode.FORBIDDEN) from None
                raise
            document = DataContractDocument.model_validate(contract.public["contract_json"])
            names = {field.name: field.column for field in document.fields}
            if (
                len(columns) != len(set(columns))
                or not set(columns) <= names.keys()
                or not set(document.business_key) <= set(columns)
            ):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            projection = {names[name] for name in columns}
            departments = principal.departments(now)
            grants = []
            for department_id in sorted(departments):
                grants.extend(
                    await transaction.find(
                        "grant",
                        data_product_id=str(product_id),
                        consumer_department_id=str(department_id),
                        status="ACTIVE",
                    )
                )
            selected = None
            for grant in sorted(grants, key=lambda item: item.public["id"]):
                data = grant.public
                department_id = uuid(data["consumer_department_id"])
                if (
                    department_id not in principal.departments(now)
                    or data["consumer_principal_id"] is not None
                    or data["status"] != "ACTIVE"
                    or data["contract_version_id"] != str(contract_id)
                    or data["applied_revision"] != data["desired_revision"]
                    or (data["expires_at"] and instant(data["expires_at"]) <= now)
                    or not projection <= set(data["scope_json"]["columns"])
                ):
                    continue
                department = await transaction.get("department", department_id)
                if department.public["status"] == "ACTIVE":
                    selected = data
                    break
            if selected is None:
                raise CoreError(ErrorCode.FORBIDDEN)
            context = TrustedHasuraContext(
                principal.principal_id,
                uuid(selected["consumer_department_id"]),
                product_id,
                contract_id,
                uuid(selected["id"]),
                selected["desired_revision"],
                selected["applied_revision"],
                now,
                instant(selected["expires_at"]),
            )
            root = product.public["object_name"]
            if product.public["schema_name"] != "public":
                root = product.public["schema_name"] + "_" + root
            target = ReadTarget(
                source.public["hasura_source_name"],
                root,
                tuple((field.column, field.name) for field in document.fields),
            )
        if self.reader is None:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        rows = await self.reader.read(
            target,
            context,
            tuple(names[name] for name in columns),
            tuple(names[name] for name in document.business_key),
            limit + 1,
            offset,
        )
        if not isinstance(rows, list) or len(rows) > limit + 1:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        validated = validate_rows(document, columns, rows)
        return {
            "data_product_id": str(product_id),
            "contract_version_id": str(contract_id),
            "columns": list(columns),
            "rows": validated[:limit],
            "limit": limit,
            "offset": offset,
            "has_more": len(validated) > limit,
        }
