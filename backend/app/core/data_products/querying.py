import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.applications.manifest import fingerprint
from app.core.data_products.reading import TrustedHasuraContext, validate_rows
from app.core.data_products.relationships import RelationshipRegistry, table_ref
from app.core.data_products.schemas import DataContractDocument
from app.core.db.commands import uuid
from app.core.governance.policy import resolve_principal
from app.core.governance.schemas import Identifier
from app.core.governance.service import current_product, instant
from app.core.hasura_provisioner.client import (
    PERMISSION_OWNER,
    matches_permission,
    matches_source,
    source_in,
    table_in,
)
from app.core.hasura_provisioner.planning import postgres_source_args
from app.core.hasura_provisioner.relationships import HasuraRelationships, RelationshipMetadata
from app.core.hasura_provisioner.schemas import DTO, SelectPermission, department_role
from app.platform.http.errors import CoreError, ErrorCode, ErrorDetail

logger = logging.getLogger(__name__)
Operator = Literal["_eq", "_neq", "_gt", "_gte", "_lt", "_lte", "_in", "_is_null"]


class QueryOrder(DTO):
    field: Identifier
    direction: Literal["asc", "desc"] = "asc"


class QuerySelection(DTO):
    select: tuple[Identifier, ...] = Field(min_length=1, max_length=100)
    contract_version_id: UUID | None = None
    where: dict[Identifier, dict[Operator, JsonValue]] = Field(default_factory=dict, max_length=50)
    order_by: tuple[QueryOrder, ...] = Field(default=(), max_length=20)
    include: dict[Identifier, "QuerySelection"] = Field(default_factory=dict, max_length=20)
    limit: int = Field(default=20, ge=1, le=100, strict=True)
    offset: int = Field(default=0, ge=0, le=10000, strict=True)


class QueryScan(DTO):
    selection: QuerySelection
    pages: int = Field(default=4, ge=1, le=4, strict=True)


class QueryLimits(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLOWBRIDGE_QUERY_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    max_depth: int = Field(default=4, ge=0, le=8)
    max_nodes: int = Field(default=20, ge=1, le=100)
    max_rows: int = Field(default=100, ge=1, le=100)
    max_array_rows: int = Field(default=20, ge=1, le=100)
    max_complexity: int = Field(default=10000, ge=1, le=1000000)
    max_request_bytes: int = Field(default=65536, ge=1024, le=1048576)
    timeout_seconds: float = Field(default=20, gt=0, le=60)


@dataclass
class QueryNode:
    product: dict
    source: dict
    contract: dict
    document: DataContractDocument
    selection: QuerySelection
    relation: dict | None = None
    children: dict[str, "QueryNode"] = field(default_factory=dict)
    required_columns: set[str] = field(default_factory=set)
    grant: dict | None = None

    @property
    def root(self):
        return (
            self.product["object_name"]
            if self.product["schema_name"] == "public"
            else self.product["schema_name"] + "_" + self.product["object_name"]
        )

    def nodes(self):
        yield self
        for child in self.children.values():
            yield from child.nodes()

    def metadata(self, child):
        return RelationshipMetadata(
            self.source["hasura_source_name"],
            table_ref(self.product),
            child.source["hasura_source_name"],
            table_ref(child.product),
            child.relation["name"],
            child.relation["relationship_type"],
            child.relation["field_mapping"],
        )


def query_shape(selection, limits, *, depth=0):
    if depth > limits.max_depth or depth == 0 and selection.limit > limits.max_rows:
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    nodes, cost = 1, len(selection.select)
    for child in selection.include.values():
        count, child_cost = query_shape(child, limits, depth=depth + 1)
        nodes, cost = nodes + count, cost + child_cost
    if nodes > limits.max_nodes or cost > limits.max_complexity:
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    return nodes, cost


def resolved_complexity(node, *, multiplicity=1):
    multiplier = multiplicity * (
        node.selection.limit
        if node.relation is None or node.relation["relationship_type"] == "array"
        else 1
    )
    return multiplier * len(node.selection.select) + sum(
        resolved_complexity(child, multiplicity=multiplier) for child in node.children.values()
    )


def validate_selection(node):
    selection, document = node.selection, node.document
    fields = {item.name: item for item in document.fields}
    selected = set(selection.select)
    if len(selected) != len(selection.select) or not selected <= fields.keys():
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    if set(selection.include) & selected:
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    order = [item.field for item in selection.order_by]
    if len(set(order)) != len(order) or not set(order) <= fields.keys():
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    node.required_columns.update(fields[name].column for name in selected | set(order))
    node.required_columns.update(fields[name].column for name in document.business_key)
    for name, predicates in selection.where.items():
        if name not in fields or not predicates:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        node.required_columns.add(fields[name].column)
        for operator, value in predicates.items():
            if operator == "_is_null":
                if type(value) is not bool:
                    raise CoreError(ErrorCode.VALIDATION_ERROR)
                continue
            if fields[name].wire_type == "boolean" and operator not in {"_eq", "_neq", "_in"}:
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            values = value if operator == "_in" else [value]
            if not isinstance(values, list) or not 1 <= len(values) <= 100:
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            for item in values:
                if item is None:
                    raise CoreError(ErrorCode.VALIDATION_ERROR)
                try:
                    validate_rows(document, (name,), [{fields[name].column: item}])
                except CoreError:
                    raise CoreError(ErrorCode.VALIDATION_ERROR) from None


def compile_query(root):
    variables, definitions = {}, []

    def variable(value, graphql_type):
        name = "v" + str(len(variables))
        variables[name] = value
        definitions.append("$" + name + ":" + graphql_type + "!")
        return "$" + name

    def selection(node, field_name, *, array):
        source = node.source["hasura_source_name"]
        type_name = source + "_" + node.root
        tokens = (source, node.root, field_name, *node.selection.select)
        if any(not re.fullmatch(r"(?!__)[a-z_][a-z0-9_]{0,62}", name) for name in tokens):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        arguments = []
        if array:
            arguments.extend(
                [
                    "limit:" + variable(node.selection.limit, "Int"),
                    "offset:" + variable(node.selection.offset, "Int"),
                ]
            )
            ordering = [{item.field: item.direction} for item in node.selection.order_by]
            ordered = {item.field for item in node.selection.order_by}
            ordering.extend(
                {key: "asc"} for key in node.document.business_key if key not in ordered
            )
            arguments.append("order_by:" + variable(ordering, "[" + type_name + "_order_by!]"))
            if node.selection.where:
                arguments.append("where:" + variable(node.selection.where, type_name + "_bool_exp"))
        projection = list(node.selection.select)
        for name, child in node.children.items():
            projection.append(
                selection(child, name, array=child.relation["relationship_type"] == "array")
            )
        return (
            field_name
            + ("(" + ",".join(arguments) + ")" if arguments else "")
            + ("{" + " ".join(projection) + "}")
        )

    body = selection(root, root.root, array=True)
    query = (
        "query CoreRelationshipRead("
        + ",".join(definitions)
        + "){"
        + (root.source["hasura_source_name"] + "{rows:" + body + "}}")
    )
    return query, variables


def validate_nested(node, rows):
    if not isinstance(rows, list) or len(rows) > node.selection.limit:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    output = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != set(node.selection.select) | node.children.keys()
        ):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        physical = {item.name: item.column for item in node.document.fields}
        clean = validate_rows(
            node.document,
            node.selection.select,
            [{physical[name]: row[name] for name in node.selection.select}],
        )[0]
        for name, child in node.children.items():
            value = row[name]
            if child.relation["relationship_type"] == "object":
                clean[name] = None if value is None else validate_nested(child, [value])[0]
            else:
                clean[name] = validate_nested(child, value)
        output.append(clean)
    return output


class RelationshipQueries:
    def __init__(self, core, metadata, limits=None):
        self.core, self.metadata = core, metadata
        self.limits = limits or QueryLimits()

    async def resolve(self, transaction, product_id, selection, relation=None):
        try:
            product, source, contract = await current_product(
                transaction, product_id, selection.contract_version_id
            )
        except CoreError as error:
            if error.code in {ErrorCode.NOT_FOUND, ErrorCode.INVALID_STATE}:
                raise CoreError(ErrorCode.FORBIDDEN) from None
            raise
        node = QueryNode(
            product.public,
            source.public,
            contract.public,
            DataContractDocument.model_validate(contract.public["contract_json"]),
            selection,
            relation,
        )
        validate_selection(node)
        for name, child_selection in selection.include.items():
            relationships = await transaction.find(
                "relationship", source_product_id=product.public["id"], name=name
            )
            if len(relationships) != 1:
                raise CoreError(ErrorCode.FORBIDDEN)
            definition = relationships[0].public
            if (
                definition["status"] != "ACTIVE"
                or definition["applied_revision"] != definition["revision"]
                or definition["source_contract_id"] != contract.public["id"]
            ):
                raise CoreError(ErrorCode.FORBIDDEN)
            if definition["relationship_type"] == "object":
                if child_selection.model_fields_set & {"where", "order_by", "limit", "offset"}:
                    raise CoreError(
                        ErrorCode.VALIDATION_ERROR,
                        details=(
                            ErrorDetail(
                                field="include",
                                reason=(
                                    "Object relationships accept selection only; "
                                    "filter, ordering and pagination require an array node."
                                ),
                            ),
                        ),
                    )
            elif child_selection.limit > min(self.limits.max_array_rows, self.limits.max_rows):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            child = await self.resolve(
                transaction, uuid(definition["target_product_id"]), child_selection, definition
            )
            if child.contract["id"] != definition["target_contract_id"]:
                raise CoreError(ErrorCode.FORBIDDEN)
            node.required_columns.update(definition["field_mapping"])
            child.required_columns.update(definition["field_mapping"].values())
            node.children[name] = child
        return node

    async def authorize(self, transaction, identity, root):
        principal = await resolve_principal(transaction, identity)
        now = await transaction.now()
        nodes = list(root.nodes())
        for department_id in sorted(principal.departments(now)):
            choices = []
            for node in nodes:
                grants = await transaction.find(
                    "grant",
                    data_product_id=node.product["id"],
                    consumer_department_id=str(department_id),
                    status="ACTIVE",
                )
                valid = []
                for grant in grants:
                    value = grant.public
                    scope = value["scope_json"]
                    if (
                        value["contract_version_id"] != node.contract["id"]
                        or value["consumer_principal_id"] is not None
                        or value["desired_revision"] != value["applied_revision"]
                        or (value["expires_at"] and instant(value["expires_at"]) <= now)
                        or scope.get("action") != "SELECT"
                        or scope.get("row_scope") != {"kind": "ALL"}
                        or not node.required_columns <= set(scope["columns"])
                    ):
                        continue
                    request = await transaction.get("request", uuid(value["access_request_id"]))
                    if request.public["status"] == "APPROVED":
                        valid.append(value)
                if len(valid) != 1:
                    break
                choices.append(valid[0])
            if len(choices) != len(nodes):
                continue
            for node, grant in zip(nodes, choices, strict=True):
                node.grant = grant
            expires = [instant(value["expires_at"]) for value in choices if value["expires_at"]]
            grant = choices[0]
            return TrustedHasuraContext(
                principal.principal_id,
                department_id,
                uuid(root.product["id"]),
                uuid(root.contract["id"]),
                uuid(grant["id"]),
                grant["desired_revision"],
                grant["applied_revision"],
                now,
                min(expires) if expires else None,
            )
        raise CoreError(ErrorCode.FORBIDDEN)

    async def verify(self, transaction, root, context):
        if self.metadata is None or self.core.reader is None:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        await self.metadata.require_consistent()
        snapshot = await self.metadata.export_metadata()
        registry = RelationshipRegistry(self.core, self.metadata)
        seen, snapshots = set(), {}
        for node in root.nodes():
            stored_source = await transaction.get("source", uuid(node.source["id"]))
            if node.source["id"] not in snapshots:
                snapshots[node.source["id"]] = await self.core.discovery.snapshot(stored_source)
            if node.product["id"] not in seen:
                await self.core.verify_contract(
                    transaction,
                    await transaction.get("product", uuid(node.product["id"])),
                    await transaction.get("contract", uuid(node.contract["id"])),
                    snapshot=snapshots[node.source["id"]],
                )
                seen.add(node.product["id"])
            source = source_in(snapshot, node.source["hasura_source_name"])
            connection = await self.core.discovery.policy.secrets.resolve_postgres(
                stored_source.private["secret_ref"]
            )
            expected_source = postgres_source_args(
                node.source["hasura_source_name"], connection.environment_name
            )
            table = table_in(source, table_ref(node.product)) if source else None
            aliases = {
                item.column: {"custom_name": item.name}
                for item in node.document.fields
                if item.column != item.name
            }
            if (
                table is None
                or not matches_source(
                    source, expected_source["configuration"], expected_source["customization"]
                )
                or source.get("customization")
                != {
                    "root_fields": {"namespace": node.source["hasura_source_name"]},
                    "type_names": {"prefix": node.source["hasura_source_name"] + "_"},
                }
                or table.get("configuration", {}).get("column_config", {}) != aliases
                or any(
                    value
                    for key, value in table.get("configuration", {}).items()
                    if key != "column_config"
                )
            ):
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            permissions = [
                entry
                for entry in table.get("select_permissions", [])
                if entry.get("role") == department_role(context.department_id)
            ]
            expected = SelectPermission(
                columns=tuple(node.grant["scope_json"]["columns"]),
                limit=self.core.reader.settings.select_limit,
            ).payload()
            if (
                len(permissions) != 1
                or permissions[0].get("comment") != PERMISSION_OWNER
                or not matches_permission(permissions[0].get("permission"), expected)
            ):
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            for child in node.children.values():
                await registry.validate(transaction, child.relation, snapshots)
                await HasuraRelationships(self.metadata).require(node.metadata(child), snapshot)
        return snapshot.resource_version

    @staticmethod
    def state_signature(root):
        return fingerprint(
            [
                [
                    node.product,
                    node.source,
                    node.contract,
                    node.relation,
                    node.grant,
                ]
                for node in root.nodes()
            ]
        )

    async def run(self, identity, product_id, selection, correlation_id, *, pages=1):
        started, actor, root = monotonic(), None, None
        outcome, code = "FAILURE", None
        try:
            if (
                type(pages) is not int
                or not 1 <= pages <= 4
                or pages > 1
                and selection.include
                or selection.offset + (pages - 1) * selection.limit > 10000
            ):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            encoded = json.dumps(selection.model_dump(mode="json")).encode()
            if len(encoded) > self.limits.max_request_bytes:
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            query_shape(selection, self.limits)
            async with asyncio.timeout(self.limits.timeout_seconds):
                async with self.core.store.transaction(read_only=True) as transaction:
                    actor = (await resolve_principal(transaction, identity)).principal_id
                    root = await self.resolve(transaction, product_id, selection)
                    if pages > 1 and not set(root.document.business_key) <= set(selection.select):
                        raise CoreError(ErrorCode.VALIDATION_ERROR)
                    if resolved_complexity(root) * pages > self.limits.max_complexity:
                        raise CoreError(ErrorCode.VALIDATION_ERROR)
                    context = await self.authorize(transaction, identity, root)
                    metadata_version = await self.verify(transaction, root, context)
                    context = await self.authorize(transaction, identity, root)
                    signature = self.state_signature(root)
                rows, has_more, seen = [], True, set()
                for index in range(pages):
                    root.selection = selection.model_copy(
                        update={"offset": selection.offset + index * selection.limit}
                    )
                    query, variables = compile_query(root)
                    result = await self.core.reader.execute_graphql(context, query, variables)
                    if set(result["data"]) != {root.source["hasura_source_name"]}:
                        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
                    namespace = result["data"].get(root.source["hasura_source_name"])
                    if not isinstance(namespace, dict) or set(namespace) != {"rows"}:
                        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
                    batch = validate_nested(root, namespace["rows"])
                    if pages > 1:
                        for row in batch:
                            key = fingerprint([row[field] for field in root.document.business_key])
                            if key in seen:
                                raise CoreError(ErrorCode.VERSION_CONFLICT)
                            seen.add(key)
                    rows.extend(batch)
                    if len(batch) < selection.limit:
                        has_more = False
                        break
                root.selection = selection
                if (await self.metadata.export_metadata()).resource_version != metadata_version:
                    raise CoreError(ErrorCode.VERSION_CONFLICT)
                await self.metadata.require_consistent()
                async with self.core.store.transaction(read_only=True) as transaction:
                    fresh = await self.resolve(transaction, product_id, selection)
                    await self.authorize(transaction, identity, fresh)
                    if self.state_signature(fresh) != signature:
                        raise CoreError(ErrorCode.FORBIDDEN)
                outcome = "SUCCESS"
                return {
                    "data_product_id": root.product["id"],
                    "contract_version_id": root.contract["id"],
                    "rows": rows,
                    "limit": selection.limit,
                    "offset": selection.offset,
                    **({"pages": pages, "has_more": has_more} if pages > 1 else {}),
                }
        except TimeoutError:
            code = ErrorCode.UPSTREAM_TIMEOUT
            raise CoreError(code) from None
        except CoreError as error:
            code = error.code
            outcome = "DENIED" if code == ErrorCode.FORBIDDEN else "FAILURE"
            raise
        except ValidationError:
            code = ErrorCode.UPSTREAM_UNAVAILABLE
            raise CoreError(code) from None
        finally:
            elapsed = round((monotonic() - started) * 1000)
            relationships = (
                sorted({node.relation["id"] for node in root.nodes() if node.relation})
                if root
                else []
            )
            paths, pending = [], [("", selection)]
            while pending and len(paths) < self.limits.max_nodes:
                prefix, node = pending.pop()
                for name, child in node.include.items():
                    path = prefix + "." + name if prefix else name
                    paths.append(path)
                    if len(paths) >= self.limits.max_nodes:
                        break
                    pending.append((path, child))
            async with self.core.store.transaction() as transaction:
                await transaction.audit(
                    actor,
                    correlation_id,
                    "data.query",
                    product_id,
                    resource_type="product",
                    outcome=outcome,
                    error_code=code.value if code else None,
                    details={
                        "scan_pages": pages,
                        "relationship_ids": relationships,
                        "paths": paths,
                        "duration_ms": elapsed,
                    },
                )
            logger.info(
                "Core query root=%s request=%s decision=%s duration_ms=%s",
                product_id,
                correlation_id,
                outcome,
                elapsed,
            )
