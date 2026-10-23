from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from app.core.data_products.schemas import (
    SEMVER,
    ContractCreate,
    DataContractDocument,
    ProductCreate,
)
from app.core.data_sources.discovery import canonical_type
from app.core.data_sources.schemas import SourceCreate
from app.core.db.commands import CommandContext, timestamp, uuid
from app.core.db.ports import CommandReceipt, StoredResource
from app.core.governance.policy import resolve_principal
from app.core.hasura_provisioner.schemas import DTO, TableRef
from app.platform.http.errors import CoreError, ErrorCode

from .manifest import ApplicationManifest, Key, fingerprint
from .planning import PlanContext, compile_manifest, qualified
from .schemas import ApplicationCreate


class ProductContractProposal(DTO):
    product_key: Key
    version: str = Field(min_length=5, max_length=30, pattern=SEMVER)
    contract_json: DataContractDocument


class OnboardingSubmission(DTO):
    manifest: ApplicationManifest
    product_contracts: tuple[ProductContractProposal, ...] = Field(min_length=1, max_length=100)


class PlanRequest(DTO):
    submission: OnboardingSubmission
    discovery_mode: Literal["STATIC", "LIVE"] = "STATIC"


class ImportRequest(DTO):
    submission: OnboardingSubmission
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_application_revision: int = Field(ge=0, strict=True)


def annotation_issues(submission: OnboardingSubmission) -> list[dict]:
    manifest = submission.manifest
    products = {product.key: product for product in manifest.data_products}
    proposals = {proposal.product_key: proposal for proposal in submission.product_contracts}
    issues = []

    def issue(path):
        issues.append(
            {
                "code": "ANNOTATION_MISMATCH",
                "path": path,
                "message": "Canonical annotations must preserve the manifest declaration.",
                "severity": "ERROR",
            }
        )

    if len(proposals) != len(submission.product_contracts) or proposals.keys() != products.keys():
        issue("product_contracts")
    for key in proposals.keys() & products.keys():
        product, proposal = products[key], proposals[key]
        fields = {field.column: field for field in proposal.contract_json.fields}
        if proposal.version != product.contract_version or fields.keys() != {
            field.column for field in product.fields
        }:
            issue(key)
            continue
        for field in product.fields:
            canonical = fields[field.column]
            if (
                canonical.name != (field.alias or field.column)
                or canonical.description != field.description
                or field.expected_type is not None
                and canonical_type(canonical.physical_type) != field.expected_type
            ):
                issue(key + "." + field.column)
        original = [
            relation.model_copy(
                update={
                    "target_product": qualified(manifest.application.key, relation.target_product)
                }
            ).model_dump(mode="json", by_alias=True)
            for relation in product.relationships
        ]
        declared = [
            relation.model_dump(mode="json", by_alias=True)
            for relation in proposal.contract_json.relationships
        ]
        if original != declared:
            issue(key + ".relationships")
    return issues


class RuntimeOnboarding:
    def __init__(self, sharing, metadata=None):
        self.sharing, self.store, self.metadata = sharing, sharing.store, metadata

    async def validate(self, identity, submission):
        async with self.store.transaction() as transaction:
            (await resolve_principal(transaction, identity)).require_admin()
        issues = annotation_issues(submission)
        return {
            "valid": not issues,
            "manifest_hash": fingerprint(submission.model_dump(mode="json", by_alias=True)),
            "issues": issues,
        }

    async def state(self, transaction, manifest):
        app_key = manifest.application.key
        departments = await transaction.find("department", code=manifest.application.department_key)
        if len(departments) != 1 or departments[0].public["status"] != "ACTIVE":
            raise CoreError(ErrorCode.INVALID_STATE)
        department_id = departments[0].public["id"]
        applications = await transaction.find("application", app_key=app_key)
        entities = {}
        if applications:
            entities["application:" + app_key] = applications[0]
        for source in manifest.sources:
            matches = await transaction.find("source", source_key=qualified(app_key, source.key))
            if matches:
                entities["source:" + qualified(app_key, source.key)] = matches[0]
        for product in manifest.data_products:
            key = qualified(app_key, product.key)
            matches = await transaction.find("product", product_key=key)
            if matches:
                entities["product:" + key] = matches[0]
                contracts = await transaction.find(
                    "contract",
                    data_product_id=matches[0].public["id"],
                    version=product.contract_version,
                )
                if contracts:
                    entities["contract:" + key + ":" + product.contract_version] = contracts[0]
        state_hash = fingerprint(
            {
                key: {"public": value.public, "private": value.private}
                for key, value in sorted(entities.items())
            }
        )
        return department_id, entities, state_hash

    @staticmethod
    def desired(submission, department_id, entities):
        manifest = submission.manifest
        app_key = manifest.application.key
        application = ApplicationCreate(
            department_id=department_id,
            app_key=app_key,
            name=manifest.application.name,
            description=manifest.application.description,
        )
        proposals = {proposal.product_key: proposal for proposal in submission.product_contracts}
        expected = {
            "application:" + app_key: application.model_dump(
                mode="json", exclude={"business_api_base_url"}
            )
        }
        for source in manifest.sources:
            expected["source:" + qualified(app_key, source.key)] = {
                "source_key": qualified(app_key, source.key),
                "host": source.host,
                "port": source.port,
                "database_name": source.database,
                "db_user": source.username,
                "ssl_mode": source.ssl_mode,
                "secret_ref": source.secret_ref,
            }
        for product in manifest.data_products:
            key = qualified(app_key, product.key)
            expected["product:" + key] = {
                "product_key": key,
                "name": product.name,
                "description": product.description,
                "schema_name": product.object.schema_name,
                "object_name": product.object.name,
                "object_type": product.object.kind.upper(),
                "classification": proposals[product.key].contract_json.classification,
            }
            expected["contract:" + key + ":" + product.contract_version] = {
                "version": product.contract_version,
                "contract_json": proposals[product.key].contract_json.model_dump(
                    mode="json", by_alias=True, exclude_unset=True
                ),
            }
        return expected

    async def prepare(self, identity, submission, mode):
        manifest = submission.manifest
        issues = annotation_issues(submission)
        async with self.store.transaction() as transaction:
            (await resolve_principal(transaction, identity)).require_admin()
            if len(manifest.sources) > 100 or len(manifest.data_products) > 100:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            now = await transaction.now()
            department_id, entities, state_hash = await self.state(transaction, manifest)
        context = PlanContext(admin_verified=True, existing_core={} if mode == "LIVE" else None)
        if mode == "LIVE":
            if self.metadata is None:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            context.metadata = await self.metadata.export_metadata()
            for source in manifest.sources:
                proposal = StoredResource(
                    {
                        "id": str(uuid("00000000-0000-0000-0000-000000000000")),
                        "hasura_source_name": "onboarding",
                        "revision": 1,
                        "status": "DRAFT",
                        "source_key": qualified(manifest.application.key, source.key),
                    },
                    {
                        "host": source.host,
                        "port": source.port,
                        "database_name": source.database,
                        "db_user": source.username,
                        "ssl_mode": source.ssl_mode,
                        "secret_ref": source.secret_ref,
                    },
                )
                snapshot = await self.sharing.discovery.snapshot(proposal)
                context.snapshots[source.key] = snapshot.model_copy(
                    update={"source_key": source.key}
                )
                connection = await self.sharing.discovery.policy.secrets.resolve_postgres(
                    source.secret_ref
                )
                context.environment_refs[source.key] = connection.environment_name
            context.approved_sources = frozenset(context.snapshots)
        plan = compile_manifest(manifest, context)
        issues.extend(item.model_dump() for item in plan.issues)
        expected = (
            self.desired(submission, department_id, entities)
            if not annotation_issues(submission)
            else {}
        )
        steps = []
        operations = {
            "application": "REGISTER_APPLICATION",
            "source": "REGISTER_SOURCE",
            "product": "REGISTER_PRODUCT",
            "contract": "REGISTER_CONTRACT",
            "hasura-source": "REGISTER_SOURCE",
            "track": "TRACK_OBJECT",
            "relationship": "CREATE_RELATIONSHIP",
        }
        for step in plan.steps:
            prefix, _, target = step.key.partition(":")
            target = target.split(":")[0]
            action, blockers = step.action, list(step.blockers)
            if step.layer == "CORE" and mode == "LIVE" and step.key in expected:
                previous = entities.get(step.key)
                actual = {**previous.public, **previous.private} if previous else {}
                same = previous and all(
                    actual.get(key) == value for key, value in expected[step.key].items()
                )
                if previous and prefix == "product":
                    declaration = next(
                        item
                        for item in manifest.data_products
                        if qualified(manifest.application.key, item.key) == target
                    )
                    binding = entities.get(
                        "source:" + qualified(manifest.application.key, declaration.source_key)
                    )
                    if binding is None or previous.public["data_source_id"] != binding.public["id"]:
                        same = False
                        issues.append(
                            {
                                "code": "PRODUCT_SOURCE_BINDING_CHANGED",
                                "path": step.key,
                                "message": "Existing products cannot be rebound by import.",
                                "severity": "ERROR",
                            }
                        )
                action = "NO_CHANGE" if same else "UPDATE" if previous else "CREATE"
                if previous and not same and previous.public["status"] != "DRAFT":
                    action, blockers = "BLOCKED", ["ACTIVE_CONFIGURATION_DRIFT"]
                    issues.append(
                        {
                            "code": "ACTIVE_CONFIGURATION_DRIFT",
                            "path": step.key,
                            "message": "Import cannot overwrite active or immutable configuration.",
                            "severity": "ERROR",
                        }
                    )
            steps.append(
                {
                    "key": step.key,
                    "layer": step.layer,
                    "action": action,
                    "verification": step.verification,
                    "operation": operations.get(prefix, "VERIFY_METADATA"),
                    "target_key": target,
                    "depends_on": list(step.depends_on),
                    "blockers": blockers,
                    "summary": operations.get(prefix, "VERIFY_METADATA").replace("_", " ").lower(),
                }
            )
        if mode == "LIVE":
            for proposal in submission.product_contracts:
                product = next(
                    (item for item in manifest.data_products if item.key == proposal.product_key),
                    None,
                )
                if product is None:
                    continue
                observed = context.snapshots[product.source_key].find(
                    TableRef(schema=product.object.schema_name, name=product.object.name)
                )
                fields = {column.name: column for column in observed.columns} if observed else {}
                if any(
                    field.column not in fields
                    or fields[field.column].pg_type.lower() != field.physical_type.lower()
                    or fields[field.column].nullable != field.nullable
                    for field in proposal.contract_json.fields
                ):
                    issues.append(
                        {
                            "code": "PHYSICAL_ANNOTATION_MISMATCH",
                            "path": proposal.product_key,
                            "message": "Physical annotations must match discovery.",
                            "severity": "ERROR",
                        }
                    )
        manifest_hash = fingerprint(submission.model_dump(mode="json", by_alias=True))
        discovery_hash = fingerprint(
            {
                key: value.model_dump(mode="json", by_alias=True)
                for key, value in sorted(context.snapshots.items())
            }
        )
        window = int(now.timestamp()) // 300
        plan_hash = fingerprint(
            [manifest_hash, discovery_hash, state_hash, plan.metadata_resource_version, window]
        )
        valid = not any(issue["severity"] == "ERROR" for issue in issues)
        if len(steps) > 1000 or len(issues) > 1000:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        result = {
            "manifest_hash": manifest_hash,
            "discovery_hash": discovery_hash,
            "plan_hash": plan_hash,
            "application_key": manifest.application.key,
            "evidence": "LIVE_VERIFIED" if mode == "LIVE" else "STATIC_UNKNOWN",
            "metadata_resource_version": plan.metadata_resource_version,
            "valid": valid,
            "ready_for_dispatch": False,
            "expires_at": timestamp(datetime.fromtimestamp((window + 1) * 300, UTC)),
            "steps": steps,
            "issues": issues,
            "approval_requirements": [
                {
                    "kind": "IT_SOURCE_REVIEW",
                    "target_key": qualified(manifest.application.key, source.key),
                    "satisfied": mode == "LIVE",
                }
                for source in manifest.sources
            ],
            "permission_policy": plan.permission_policy,
        }
        return result, state_hash, entities, department_id

    async def plan(self, identity, request):
        return (await self.prepare(identity, request.submission, request.discovery_mode))[0]

    async def import_drafts(self, identity, request, method, path, key, correlation_id):
        payload = request.model_dump(mode="json", by_alias=True, exclude_unset=True)
        namespace = fingerprint([identity.issuer, identity.subject, method, path, key])
        async with self.store.transaction() as transaction:
            (await resolve_principal(transaction, identity)).require_admin()
            receipt = await transaction.receipt(namespace)
            if receipt:
                if receipt.fingerprint != fingerprint(payload):
                    raise CoreError(ErrorCode.IDEMPOTENCY_CONFLICT)
                return receipt
        plan, state_hash, entities, department_id = await self.prepare(
            identity, request.submission, "LIVE"
        )

        async def save(context: CommandContext):
            current_department, current, current_hash = await self.state(
                context.transaction, request.submission.manifest
            )
            if (
                plan["plan_hash"] != request.plan_hash
                or current_hash != state_hash
                or current_department != department_id
                or not plan["valid"]
                or datetime.fromisoformat(plan["expires_at"].replace("Z", "+00:00")) <= context.now
            ):
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            manifest = request.submission.manifest
            application = entities.get("application:" + manifest.application.key)
            if (
                application.public["revision"] if application else 0
            ) != request.expected_application_revision:
                raise CoreError(ErrorCode.VERSION_CONFLICT)
            expected = self.desired(request.submission, department_id, entities)

            changed = False

            async def upsert(kind, logical_key, values, create):
                nonlocal changed
                previous = current.get(logical_key)
                if previous is None:
                    result = await create()
                    changed = True
                    return result.body["id"]
                actual = {**previous.public, **previous.private}
                if all(actual.get(name) == value for name, value in values.items()):
                    return previous.public["id"]
                if previous.public["status"] != "DRAFT":
                    raise CoreError(ErrorCode.INVALID_STATE)
                public = {name: value for name, value in values.items() if name in previous.public}
                private = {
                    **previous.private,
                    **{name: value for name, value in values.items() if name in previous.private},
                }
                replacement = context.update(previous, **public)
                await context.transaction.replace(kind, StoredResource(replacement.public, private))
                changed = True
                return previous.public["id"]

            app_key = "application:" + manifest.application.key
            app_id = await upsert(
                "application",
                app_key,
                expected[app_key],
                lambda: self.sharing._create_application(
                    context, {**expected[app_key], "business_api_base_url": None}
                ),
            )
            source_ids, product_ids, contract_ids = {}, [], []
            for source in manifest.sources:
                source_key = "source:" + qualified(manifest.application.key, source.key)
                body = SourceCreate(
                    application_id=app_id, db_type="POSTGRESQL", **expected[source_key]
                ).model_dump(mode="json")
                source_ids[source.key] = await upsert(
                    "source",
                    source_key,
                    expected[source_key],
                    lambda body=body: self.sharing._create_source(context, body),
                )
            proposals = {item.product_key: item for item in request.submission.product_contracts}
            for product in manifest.data_products:
                product_key = "product:" + qualified(manifest.application.key, product.key)
                body = ProductCreate(
                    data_source_id=source_ids[product.source_key], **expected[product_key]
                ).model_dump(mode="json")
                product_id = await upsert(
                    "product",
                    product_key,
                    expected[product_key],
                    lambda body=body: self.sharing._create_product(context, body),
                )
                product_ids.append(product_id)
                contract_key = (
                    "contract:"
                    + qualified(manifest.application.key, product.key)
                    + ":"
                    + product.contract_version
                )
                body = ContractCreate(
                    version=proposals[product.key].version,
                    contract_json=proposals[product.key].contract_json,
                ).model_dump(mode="json", by_alias=True, exclude_unset=True)
                contract_id = await upsert(
                    "contract",
                    contract_key,
                    expected[contract_key],
                    lambda body=body, product_id=product_id: self.sharing._create_contract(
                        context, body, uuid(product_id)
                    ),
                )
                contract_ids.append(contract_id)
            if application is not None and changed:
                stored_application = await context.transaction.get("application", uuid(app_id))
                if stored_application.public["revision"] == application.public["revision"]:
                    await context.transaction.replace(
                        "application", context.update(stored_application)
                    )
            await context.transaction.audit(
                context.principal.principal_id,
                correlation_id,
                "onboarding.import",
                uuid(app_id),
                resource_type="application",
                content_hash=plan["manifest_hash"],
            )
            result = {
                "application_id": app_id,
                "application_revision": (
                    await context.transaction.get("application", uuid(app_id))
                ).public["revision"],
                "manifest_hash": plan["manifest_hash"],
                "data_source_ids": list(source_ids.values()),
                "data_product_ids": product_ids,
                "contract_version_ids": contract_ids,
                "result": "DRAFTS_SAVED",
            }
            return CommandReceipt("", 201, result)

        return await self.sharing.commands.execute(
            identity, method, path, key, payload, correlation_id, save
        )
