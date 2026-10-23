from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, get_args

from pydantic import Field, TypeAdapter, ValidationError

from app.core.data_sources.discovery import (
    DiscoveredObject,
    ForeignKey,
    SourceSnapshot,
    canonical_type,
)
from app.core.hasura_provisioner.client import matches_source, source_in, table_in
from app.core.hasura_provisioner.planning import postgres_source_args, relationship_operation
from app.core.hasura_provisioner.schemas import DTO, EnvironmentName, MetadataSnapshot, TableRef

from .manifest import (
    Application,
    ApplicationManifest,
    DataType,
    Relationship,
    Source,
    fingerprint,
    source_name,
)


class Issue(DTO):
    code: str
    path: str
    message: str
    severity: Literal["ERROR", "UNKNOWN", "APPROVAL"] = "ERROR"


class ResolvedField(DTO):
    column: str
    name: str
    pg_type: str | None
    nullable: bool | None
    description: str | None


class ProductIR(DTO):
    key: str
    source_key: str
    table: TableRef
    kind: Literal["table", "view"]
    contract_version: str
    fields: tuple[ResolvedField, ...]
    relationships: tuple[Relationship, ...]


class NormalizedIR(DTO):
    manifest_version: Literal["1.0"] = "1.0"
    application: Application
    sources: tuple[Source, ...]
    products: tuple[ProductIR, ...]


class ExternalProduct(DTO):
    key: str
    source_key: str
    hasura_source_name: str
    table: TableRef
    columns: tuple[str, ...]
    published: bool


class UniquenessApproval(DTO):
    product_key: str
    columns: tuple[str, ...]
    snapshot_hash: str
    expires_at: datetime


@dataclass
class PlanContext:
    snapshots: dict[str, SourceSnapshot] = field(default_factory=dict)
    existing_core: dict[str, str] | None = None
    contract_states: dict[str, str] = field(default_factory=dict)
    metadata: MetadataSnapshot | None = None
    environment_refs: dict[str, str] = field(default_factory=dict)
    admin_verified: bool = False
    approved_sources: frozenset[str] = frozenset()
    approved_products: frozenset[str] = frozenset()
    published_products: frozenset[str] = frozenset()
    external_products: dict[str, ExternalProduct] = field(default_factory=dict)
    uniqueness: dict[str, UniquenessApproval] = field(default_factory=dict)
    engine_profile: Literal["unknown", "hasura-v2.48.16-ce"] = "unknown"


class PlanStep(DTO):
    key: str
    layer: Literal["CORE", "HASURA"]
    action: Literal["CREATE", "UPDATE", "NO_CHANGE", "BLOCKED"]
    verification: Literal["KNOWN", "UNKNOWN"]
    depends_on: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    payload: dict[str, Any] = Field(repr=False)


class OnboardingPlan(DTO):
    manifest_hash: str
    discovery_hash: str
    ir: NormalizedIR
    steps: tuple[PlanStep, ...]
    issues: tuple[Issue, ...]
    metadata_resource_version: int | None
    permission_policy: Literal["CORE_GRANTS_REQUIRED_NO_PERMISSIONS_GENERATED"] = (
        "CORE_GRANTS_REQUIRED_NO_PERMISSIONS_GENERATED"
    )

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "ERROR" for issue in self.issues)

    @property
    def ready_for_dispatch(self) -> bool:
        return self.valid and all(
            step.action != "BLOCKED" and (step.layer != "CORE" or step.action == "NO_CHANGE")
            for step in self.steps
        )


def qualified(application: str, key: str) -> str:
    return key if "." in key else f"{application}.{key}"


def object_in(context: PlanContext, source: str, table: TableRef) -> DiscoveredObject | None:
    snapshot = context.snapshots.get(source)
    if snapshot is None or snapshot.source_key != source:
        return None
    return snapshot.find(table)


def matching_fk(
    local: DiscoveredObject, target: DiscoveredObject, relationship: Relationship
) -> ForeignKey | None:
    pairs = {(item.local, item.remote) for item in relationship.mapping}
    if relationship.type == "object":
        return next(
            (
                fk
                for fk in local.foreign_keys
                if fk.target == target.table
                and set(zip(fk.columns, fk.target_columns, strict=True)) == pairs
            ),
            None,
        )
    return next(
        (
            fk
            for fk in target.foreign_keys
            if fk.target == local.table
            and set(zip(fk.target_columns, fk.columns, strict=True)) == pairs
        ),
        None,
    )


def cardinality_proven(
    context: PlanContext,
    relationship: Relationship,
    key: str,
    snapshot: SourceSnapshot,
    target: DiscoveredObject,
) -> bool:
    columns = {pair.remote for pair in relationship.mapping}
    if any(
        key_columns and set(key_columns) <= columns
        for key_columns in (target.primary_key, *target.unique_constraints)
    ):
        return True
    proof = context.uniqueness.get(relationship.uniqueness_ref or "")
    return bool(
        proof is not None
        and context.admin_verified
        and proof.product_key == key
        and set(proof.columns) == columns
        and proof.expires_at.utcoffset() is not None
        and proof.expires_at > datetime.now(UTC)
        and proof.snapshot_hash == fingerprint(snapshot.model_dump(mode="json", by_alias=True))
    )


def compile_manifest(
    manifest: ApplicationManifest, context: PlanContext | None = None
) -> OnboardingPlan:
    context = context or PlanContext()
    app = manifest.application.key
    issues: list[Issue] = []
    steps: list[PlanStep] = []
    products: list[ProductIR] = []
    product_by_key = {qualified(app, item.key): item for item in manifest.data_products}
    engine_names = {source.key: source_name(app, source.key) for source in manifest.sources}

    def issue(code: str, path: str, message: str, severity: str = "ERROR") -> str:
        issues.append(Issue(code=code, path=path, message=message, severity=severity))
        return code

    def core_step(key: str, payload: dict, dependencies: tuple[str, ...] = ()) -> None:
        known = context.existing_core is not None
        existing = (context.existing_core or {}).get(key)
        action = (
            "CREATE"
            if existing is None
            else "NO_CHANGE"
            if existing == fingerprint(payload)
            else "UPDATE"
        )
        blockers = () if known else ("CORE_STATE_UNKNOWN",)
        if (
            key.startswith("contract:")
            and action == "UPDATE"
            and context.contract_states.get(key) != "DRAFT"
        ):
            blockers += (
                issue("IMMUTABLE_CONTRACT", key, "Bump contract version for changed content"),
            )
        steps.append(
            PlanStep(
                key=key,
                layer="CORE",
                action=action if not blockers else "BLOCKED",
                verification="KNOWN" if known else "UNKNOWN",
                depends_on=dependencies,
                blockers=blockers,
                payload=payload,
            )
        )

    def engine_step(
        key: str,
        payload: dict,
        dependencies: tuple[str, ...],
        blockers: list[str],
        existing: bool | None = None,
        same: bool = False,
    ) -> None:
        known = context.metadata is not None
        if not known:
            blockers.append("HASURA_STATE_UNKNOWN")
        if not context.admin_verified:
            blockers.append("IT_ADMIN_NOT_VERIFIED")
        action = "NO_CHANGE" if same else "UPDATE" if existing else "CREATE"
        if existing and not same:
            blockers.append("CONFIG_DRIFT_REQUIRES_REVIEW")
        steps.append(
            PlanStep(
                key=key,
                layer="HASURA",
                action=action if not blockers else "BLOCKED",
                verification="KNOWN" if known else "UNKNOWN",
                depends_on=dependencies,
                blockers=tuple(sorted(set(blockers))),
                payload=payload,
            )
        )

    core_step("application:" + app, manifest.application.model_dump())
    for source in manifest.sources:
        core_step(
            "source:" + qualified(app, source.key),
            {
                **source.model_dump(),
                "source_key": qualified(app, source.key),
                "hasura_source_name": engine_names[source.key],
            },
            ("application:" + app,),
        )
        env = context.environment_refs.get(source.key)
        blockers = []
        if env is not None:
            try:
                env = TypeAdapter(EnvironmentName).validate_python(env)
            except ValidationError:
                env = None
        if env is None:
            blockers.append(
                issue(
                    "SECRET_BINDING_UNKNOWN",
                    source.key,
                    "Operator must resolve secret_ref to a Hasura environment name",
                    "UNKNOWN",
                )
            )
        if source.key not in context.approved_sources:
            blockers.append("SOURCE_APPROVAL_REQUIRED")
        args = postgres_source_args(engine_names[source.key], env or "UNRESOLVED_SOURCE_URL")
        registered = (
            source_in(context.metadata, engine_names[source.key]) if context.metadata else None
        )
        engine_step(
            "hasura-source:" + source.key,
            {"type": "pg_add_source", "version": 1, "args": args},
            ("source:" + qualified(app, source.key),),
            blockers,
            registered is not None,
            matches_source(registered, args["configuration"], args["customization"]),
        )

    for product in manifest.data_products:
        key = qualified(app, product.key)
        table = TableRef(schema=product.object.schema_name, name=product.object.name)
        observed = object_in(context, product.source_key, table)
        blockers = []
        if observed is None:
            known = product.source_key in context.snapshots
            blockers.append(
                issue(
                    "OBJECT_NOT_FOUND" if known else "DISCOVERY_UNKNOWN",
                    key,
                    f"{table.schema_name}.{table.name} not found"
                    if known
                    else "Discovery not verified",
                    "ERROR" if known else "UNKNOWN",
                )
            )
        elif observed.kind != product.object.kind:
            blockers.append(
                issue("OBJECT_KIND_MISMATCH", key, "Declared table/view kind differs from source")
            )
        physical = {column.name: column for column in observed.columns} if observed else {}
        resolved = []
        for declaration in product.fields:
            column = physical.get(declaration.column)
            if column is not None and canonical_type(column.pg_type) not in get_args(DataType):
                blockers.append(
                    issue(
                        "UNSUPPORTED_COLUMN_TYPE",
                        key,
                        f"Column {declaration.column} needs type support review",
                    )
                )
            if observed is not None and column is None:
                blockers.append(
                    issue("COLUMN_NOT_FOUND", key, f"Column {declaration.column} does not exist")
                )
            if (
                column is not None
                and declaration.expected_type is not None
                and canonical_type(column.pg_type) != declaration.expected_type
            ):
                blockers.append(
                    issue("COLUMN_TYPE_MISMATCH", key, f"Column {declaration.column} type mismatch")
                )
            public_name = declaration.alias or declaration.column
            if public_name != declaration.column and public_name in physical:
                blockers.append(
                    issue("PUBLIC_NAME_CONFLICT", key, "Alias conflicts with a physical column")
                )
            resolved.append(
                ResolvedField(
                    column=declaration.column,
                    name=public_name,
                    pg_type=column.pg_type if column else None,
                    nullable=column.nullable if column else None,
                    description=declaration.description,
                )
            )
        ir = ProductIR(
            key=key,
            source_key=product.source_key,
            table=table,
            kind=product.object.kind,
            contract_version=product.contract_version,
            fields=tuple(resolved),
            relationships=tuple(
                item.model_copy(update={"target_product": qualified(app, item.target_product)})
                for item in product.relationships
            ),
        )
        products.append(ir)
        core_step(
            "product:" + key,
            {
                "product_key": key,
                "source_key": qualified(app, product.source_key),
                "schema_name": table.schema_name,
                "object_name": table.name,
                "object_type": product.object.kind.upper(),
                "name": product.name,
                "description": product.description,
                "requested_state": product.requested_state,
            },
            ("source:" + qualified(app, product.source_key),),
        )
        core_step(
            "contract:" + key + ":" + product.contract_version,
            {
                "product_key": key,
                "version": product.contract_version,
                "fields": [
                    {
                        **item.model_dump(),
                        "type": canonical_type(item.pg_type) if item.pg_type else None,
                    }
                    for item in resolved
                ],
                "relationships": [item.model_dump(by_alias=True) for item in ir.relationships],
            },
            ("product:" + key,),
        )
        if key not in context.approved_products or product.requested_state != "PUBLISH_REQUESTED":
            blockers.append("PRODUCT_APPROVAL_REQUIRED")
        args = {"source": engine_names[product.source_key], "table": table.payload()}
        config = {
            field.column: {"custom_name": field.alias} for field in product.fields if field.alias
        }
        if config:
            args["configuration"] = {"column_config": config}
        source = (
            source_in(context.metadata, engine_names[product.source_key])
            if context.metadata
            else None
        )
        tracked = table_in(source, table) if source else None
        same = tracked is not None and (
            tracked.get("configuration", {}).get("column_config", {}) == config
        )
        operation = (
            "pg_set_table_customization" if tracked is not None and not same else "pg_track_table"
        )
        if operation == "pg_set_table_customization":
            args["configuration"] = {"column_config": config}
        engine_step(
            "track:" + key,
            {"type": operation, "version": 1, "args": args},
            (
                "hasura-source:" + product.source_key,
                "contract:" + key + ":" + product.contract_version,
            ),
            blockers,
            tracked is not None,
            same,
        )

    for product, ir in zip(manifest.data_products, products, strict=True):
        for relation in product.relationships:
            path = ir.key + "." + relation.name
            target_key = qualified(app, relation.target_product)
            local_target = product_by_key.get(target_key)
            external = context.external_products.get(target_key)
            if local_target is None and external is None:
                issue(
                    "TARGET_UNKNOWN", path, "External product must be resolved from Core", "UNKNOWN"
                )
                steps.append(
                    PlanStep(
                        key="relationship:" + path,
                        layer="HASURA",
                        action="BLOCKED",
                        verification="UNKNOWN",
                        blockers=("TARGET_UNKNOWN",),
                        payload={},
                    )
                )
                continue
            target_source = local_target.source_key if local_target else external.source_key
            target_table = (
                TableRef(schema=local_target.object.schema_name, name=local_target.object.name)
                if local_target
                else external.table
            )
            target_engine = (
                engine_names[target_source] if local_target else external.hasura_source_name
            )
            target_fields = (
                {field.column for field in local_target.fields}
                if local_target
                else set(external.columns)
            )
            local = object_in(context, product.source_key, ir.table)
            remote = object_in(context, target_source, target_table)
            mapping = {pair.local: pair.remote for pair in relation.mapping}
            blockers = []
            fk = None
            if local is None or remote is None:
                blockers.append(
                    issue(
                        "RELATIONSHIP_DISCOVERY_UNKNOWN",
                        path,
                        "Both physical objects must be discovered",
                        "UNKNOWN",
                    )
                )
            else:
                columns = {column.name: column for column in local.columns}
                remote_columns = {column.name: column for column in remote.columns}
                if relation.name in columns:
                    blockers.append(
                        issue(
                            "RELATIONSHIP_NAME_CONFLICT",
                            path,
                            "Relationship conflicts with physical column",
                        )
                    )
                for left, right in mapping.items():
                    if left not in columns or right not in remote_columns:
                        blockers.append(
                            issue("JOIN_COLUMN_MISSING", path, "Join column does not exist")
                        )
                    elif canonical_type(columns[left].pg_type) != canonical_type(
                        remote_columns[right].pg_type
                    ):
                        blockers.append(
                            issue("JOIN_TYPE_MISMATCH", path, "Join column types differ")
                        )
                if (
                    not set(mapping) <= {field.column for field in product.fields}
                    or not set(mapping.values()) <= target_fields
                ):
                    blockers.append(
                        issue(
                            "JOIN_FIELD_NOT_PUBLISHED",
                            path,
                            "Join keys must be declared contract fields",
                        )
                    )
                same_source = engine_names[product.source_key] == target_engine
                if same_source != (relation.strategy != "remote"):
                    blockers.append(
                        issue(
                            "RELATIONSHIP_STRATEGY_MISMATCH",
                            path,
                            "Cross-source requires remote; same-source requires fk/manual",
                        )
                    )
                if relation.strategy == "fk":
                    fk = (
                        matching_fk(local, remote, relation)
                        if local.kind == remote.kind == "table"
                        else None
                    )
                    if fk is None:
                        blockers.append(
                            issue("FK_NOT_FOUND", path, "No exact physical FK matches this mapping")
                        )
                if relation.type == "object" and not (
                    fk is not None
                    or cardinality_proven(
                        context, relation, target_key, context.snapshots[target_source], remote
                    )
                ):
                    blockers.append(
                        issue(
                            "CARDINALITY_UNPROVEN",
                            path,
                            "Object target must be unique; IT-reviewed evidence required",
                            "APPROVAL",
                        )
                    )
            if relation.strategy == "remote" and context.engine_profile != "hasura-v2.48.16-ce":
                blockers.append(
                    issue(
                        "UNSUPPORTED_ENGINE_PROFILE",
                        path,
                        "Remote relationship capability not verified",
                        "UNKNOWN",
                    )
                )
            if target_key not in context.published_products and not (
                external and external.published
            ):
                blockers.append("TARGET_PUBLICATION_REQUIRED")
            registered_target = (
                source_in(context.metadata, target_engine) if context.metadata else None
            )
            if registered_target is None or table_in(registered_target, target_table) is None:
                blockers.append("TARGET_NOT_TRACKED")
            if ir.key not in context.approved_products:
                blockers.append("PRODUCT_APPROVAL_REQUIRED")
            payload = relationship_operation(
                engine_names[product.source_key],
                ir.table,
                relation.name,
                relation.type,
                target_engine,
                target_table,
                mapping,
                relation.strategy,
                fk.columns if fk else (),
            )
            source = (
                source_in(context.metadata, engine_names[product.source_key])
                if context.metadata
                else None
            )
            tracked = table_in(source, ir.table) if source else None
            group = (
                "remote_relationships"
                if relation.strategy == "remote"
                else relation.type + "_relationships"
            )
            existing = next(
                (item for item in (tracked or {}).get(group, []) if item["name"] == relation.name),
                None,
            )
            if any(
                item.get("name") == relation.name
                for other in ("object_relationships", "array_relationships", "remote_relationships")
                if other != group
                for item in (tracked or {}).get(other, [])
            ):
                blockers.append(
                    issue(
                        "RELATIONSHIP_NAME_CONFLICT",
                        path,
                        "A different relationship already uses this name",
                    )
                )
            desired = payload["args"]
            body = "definition" if relation.strategy == "remote" else "using"
            same = existing is not None and existing.get(body) == desired[body]
            engine_step(
                "relationship:" + path,
                payload,
                ("track:" + ir.key, "track:" + target_key),
                blockers,
                existing is not None,
                same,
            )
    resolved_steps = []
    resolved_by_key = {}
    external_tracks = {
        "track:" + value.key for value in context.external_products.values() if value.published
    }
    for step in steps:
        blockers = list(step.blockers)
        for dependency in step.depends_on:
            if dependency in external_tracks:
                continue
            previous = resolved_by_key.get(dependency)
            if previous is None or previous.action == "BLOCKED":
                blockers.append("DEPENDENCY_BLOCKED:" + dependency)
        resolved = step.model_copy(
            update={
                "action": "BLOCKED" if blockers else step.action,
                "blockers": tuple(sorted(set(blockers))),
            }
        )
        resolved_steps.append(resolved)
        resolved_by_key[step.key] = resolved
    return OnboardingPlan(
        manifest_hash=fingerprint(manifest.model_dump(mode="json", by_alias=True)),
        discovery_hash=fingerprint(
            {
                key: snapshot.model_dump(mode="json", by_alias=True)
                for key, snapshot in sorted(context.snapshots.items())
            }
        ),
        ir=NormalizedIR(
            application=manifest.application, sources=manifest.sources, products=tuple(products)
        ),
        steps=tuple(resolved_steps),
        issues=tuple(issues),
        metadata_resource_version=context.metadata.resource_version if context.metadata else None,
    )
