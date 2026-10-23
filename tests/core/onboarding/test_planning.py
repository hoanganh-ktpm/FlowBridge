import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.core.applications.manifest import fingerprint, parse_manifest, source_name
from app.core.applications.planning import (
    ExternalProduct,
    PlanContext,
    UniquenessApproval,
    compile_manifest,
)
from app.core.data_sources.discovery import SourceSnapshot
from app.core.hasura_provisioner.planning import postgres_source_args
from app.core.hasura_provisioner.schemas import MetadataSnapshot, TableRef

from .test_manifest import EXAMPLES, warehouse


def discovery_value():
    return json.loads((EXAMPLES / "warehouse-discovery.json").read_text())["snapshots"][0]


def context_for(value=None, snapshot_value=None):
    value = value or warehouse()
    snapshot = SourceSnapshot.model_validate(snapshot_value or discovery_value())
    app = value["application"]["key"]
    key = value["sources"][0]["key"]
    name = source_name(app, key)
    args = postgres_source_args(name, "WAREHOUSE_READ_DATABASE_URL")
    return PlanContext(
        snapshots={key: snapshot},
        existing_core={},
        metadata=MetadataSnapshot(
            resource_version=8,
            metadata={
                "version": 3,
                "sources": [
                    {
                        "name": name,
                        "kind": "postgres",
                        "configuration": args["configuration"],
                        "customization": args["customization"],
                        "tables": [{"table": item.table.payload()} for item in snapshot.objects],
                    }
                ],
            },
        ),
        environment_refs={key: "WAREHOUSE_READ_DATABASE_URL"},
        admin_verified=True,
        approved_sources=frozenset({key}),
        approved_products=frozenset(
            f"{app}.{product['key']}" for product in value["data_products"]
        ),
        published_products=frozenset(
            f"{app}.{product['key']}" for product in value["data_products"]
        ),
        engine_profile="hasura-v2.48.16-ce",
    )


def relationship_step(plan):
    return next(step for step in plan.steps if step.key.startswith("relationship:"))


def approve_view_uniqueness(context):
    snapshot = context.snapshots["warehouse-pg"]
    context.uniqueness["warehouse/catalog-sku-unique"] = UniquenessApproval(
        product_key="warehouse.product_catalog",
        columns=("sku",),
        snapshot_hash=fingerprint(snapshot.model_dump(mode="json", by_alias=True)),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def test_warehouse_ir_contract_manual_relationship_and_no_permissions():
    manifest = parse_manifest(json.dumps(warehouse()))
    context = context_for()
    approve_view_uniqueness(context)
    plan = compile_manifest(manifest, context)
    step = relationship_step(plan)
    assert plan.valid
    assert step.action == "CREATE"
    assert step.payload == {
        "type": "pg_create_object_relationship",
        "version": 1,
        "args": {
            "source": source_name("warehouse", "warehouse-pg"),
            "table": {"schema": "public", "name": "stock_available"},
            "name": "product",
            "using": {
                "manual_configuration": {
                    "remote_table": {"schema": "public", "name": "product_catalog"},
                    "column_mapping": {"sku": "sku"},
                }
            },
        },
    }
    contract = next(
        step for step in plan.steps if step.key == "contract:warehouse.available_inventory:1.0.0"
    )
    assert contract.payload["fields"][1]["type"] == "integer"
    assert contract.payload["relationships"][0]["target_product"] == "warehouse.product_catalog"
    assert plan.ir.products[1].fields[0].nullable is False
    assert not any("permission" in step.payload.get("type", "") for step in plan.steps)
    position = {step.key: index for index, step in enumerate(plan.steps)}
    for step in plan.steps:
        for dependency in step.depends_on:
            assert position[dependency] < position[step.key]


def test_manifest_uniqueness_reference_is_not_owner_approval():
    plan = compile_manifest(parse_manifest(json.dumps(warehouse())), context_for())
    assert relationship_step(plan).action == "BLOCKED"
    assert "CARDINALITY_UNPROVEN" in relationship_step(plan).blockers


def test_offline_plan_reports_unknown_and_approval_gates():
    plan = compile_manifest(parse_manifest(json.dumps(warehouse())))
    assert plan.valid
    assert any(issue.code == "DISCOVERY_UNKNOWN" for issue in plan.issues)
    assert all(step.action == "BLOCKED" for step in plan.steps)
    assert plan.metadata_resource_version is None


@pytest.mark.parametrize(
    "change,code",
    [
        (
            lambda value: value["data_products"][1]["object"].update(name="missing"),
            "OBJECT_NOT_FOUND",
        ),
        (
            lambda value: value["data_products"][1]["object"].update(kind="table"),
            "OBJECT_KIND_MISMATCH",
        ),
        (
            lambda value: value["data_products"][1]["fields"][1].update(column="missing"),
            "COLUMN_NOT_FOUND",
        ),
        (
            lambda value: value["data_products"][1]["fields"][1].update(expected_type="text"),
            "COLUMN_TYPE_MISMATCH",
        ),
        (
            lambda value: value["data_products"][1]["relationships"][0]["mapping"][0].update(
                to="missing"
            ),
            "JOIN_COLUMN_MISSING",
        ),
    ],
)
def test_invalid_physical_schema(change, code):
    value = warehouse()
    context = context_for()
    change(value)
    plan = compile_manifest(parse_manifest(json.dumps(value)), context)
    assert not plan.valid
    assert any(issue.code == code for issue in plan.issues)


def test_join_type_mismatch():
    snapshot = discovery_value()
    snapshot["objects"][0]["columns"][0]["pg_type"] = "INTEGER"
    plan = compile_manifest(
        parse_manifest(json.dumps(warehouse())), context_for(snapshot_value=snapshot)
    )
    assert "JOIN_TYPE_MISMATCH" in relationship_step(plan).blockers


def test_unsupported_column_type_requires_explicit_support():
    snapshot = discovery_value()
    snapshot["objects"][1]["columns"][1]["pg_type"] = "INTEGER[]"
    plan = compile_manifest(
        parse_manifest(json.dumps(warehouse())), context_for(snapshot_value=snapshot)
    )
    assert not plan.valid
    assert any(issue.code == "UNSUPPORTED_COLUMN_TYPE" for issue in plan.issues)


def test_core_source_keys_are_namespaced_by_application():
    first = compile_manifest(parse_manifest(json.dumps(warehouse())), context_for())
    value = warehouse()
    value["application"]["key"] = "other_app"
    second = compile_manifest(parse_manifest(json.dumps(value)), context_for(value))
    for plan, app in ((first, "warehouse"), (second, "other_app")):
        step = next(step for step in plan.steps if step.key == f"source:{app}.warehouse-pg")
        assert step.payload["source_key"] == f"{app}.warehouse-pg"


def fk_case(array=False, composite=False):
    value = warehouse()
    snapshot = discovery_value()
    for product in value["data_products"]:
        product["object"]["kind"] = "table"
    for item in snapshot["objects"]:
        item["kind"] = "table"
    columns = ["sku"]
    if composite:
        columns.append("tenant_id")
        for product in value["data_products"]:
            product["fields"].append({"column": "tenant_id"})
        for item in snapshot["objects"]:
            item["columns"].append({"name": "tenant_id", "pg_type": "UUID", "nullable": False})
    snapshot["objects"][0]["primary_key"] = columns
    snapshot["objects"][1]["foreign_keys"] = [
        {
            "columns": columns,
            "target": {"schema": "public", "name": "product_catalog"},
            "target_columns": columns,
        }
    ]
    relationship = value["data_products"][1]["relationships"][0]
    relationship.update(strategy="fk", mapping=[{"from": col, "to": col} for col in columns])
    if array:
        relationship.update(name="inventory", type="array", target_product="available_inventory")
        value["data_products"][1]["relationships"] = []
        value["data_products"][0]["relationships"] = [relationship]
    return value, snapshot


@pytest.mark.parametrize(
    "array,composite", [(False, False), (False, True), (True, False), (True, True)]
)
def test_fk_object_array_and_composite_payloads(array, composite):
    value, snapshot = fk_case(array, composite)
    plan = compile_manifest(parse_manifest(json.dumps(value)), context_for(value, snapshot))
    step = relationship_step(plan)
    assert step.action == "CREATE"
    columns = ["sku", "tenant_id"] if composite else ["sku"]
    expected = (
        {"table": {"schema": "public", "name": "stock_available"}, "columns": columns}
        if array
        else columns
    )
    assert step.payload["args"]["using"] == {"foreign_key_constraint_on": expected}
    assert step.payload["type"] == f"pg_create_{'array' if array else 'object'}_relationship"


def test_missing_fk_is_not_inferred_from_equal_column_names():
    value, snapshot = fk_case()
    snapshot["objects"][1]["foreign_keys"] = []
    plan = compile_manifest(parse_manifest(json.dumps(value)), context_for(value, snapshot))
    assert "FK_NOT_FOUND" in relationship_step(plan).blockers


def test_idempotent_plan_and_contract_drift():
    manifest = parse_manifest(json.dumps(warehouse()))
    context = context_for()
    approve_view_uniqueness(context)
    first = compile_manifest(manifest, context)
    context.existing_core = {
        step.key: fingerprint(step.payload) for step in first.steps if step.layer == "CORE"
    }
    relation = relationship_step(first).payload["args"]
    context.metadata.metadata["sources"][0]["tables"][1]["object_relationships"] = [
        {
            "name": relation["name"],
            "using": relation["using"],
        }
    ]
    second = compile_manifest(manifest, context)
    assert all(step.action == "NO_CHANGE" for step in second.steps)
    assert second.manifest_hash == first.manifest_hash
    value = warehouse()
    value["data_products"][0]["fields"][0]["description"] = "Changed semantics"
    drift = compile_manifest(parse_manifest(json.dumps(value)), context)
    assert any(issue.code == "IMMUTABLE_CONTRACT" for issue in drift.issues)


def test_expired_or_stale_cardinality_evidence_is_blocked():
    context = context_for()
    approve_view_uniqueness(context)
    proof = context.uniqueness["warehouse/catalog-sku-unique"]
    context.uniqueness["warehouse/catalog-sku-unique"] = proof.model_copy(
        update={"snapshot_hash": "outdated"}
    )
    plan = compile_manifest(parse_manifest(json.dumps(warehouse())), context)
    assert "CARDINALITY_UNPROVEN" in relationship_step(plan).blockers


def test_cross_source_remote_relationship_and_capability_gate():
    value = warehouse()
    value["data_products"][1]["relationships"][0].update(
        target_product="external.catalog", strategy="remote"
    )
    context = context_for(value)
    target = deepcopy(discovery_value())
    target["source_key"] = "external-source"
    target["objects"][0]["primary_key"] = ["sku"]
    context.snapshots["external-source"] = SourceSnapshot.model_validate(target)
    context.external_products["external.catalog"] = ExternalProduct(
        key="external.catalog",
        source_key="external-source",
        hasura_source_name="external_pg",
        table=TableRef(schema="public", name="product_catalog"),
        columns=("sku", "name"),
        published=True,
    )
    context.metadata.metadata["sources"].append(
        {
            "name": "external_pg",
            "kind": "postgres",
            "tables": [{"table": {"schema": "public", "name": "product_catalog"}}],
        }
    )
    plan = compile_manifest(parse_manifest(json.dumps(value)), context)
    step = relationship_step(plan)
    assert step.action == "CREATE"
    assert step.payload["type"] == "pg_create_remote_relationship"
    assert step.payload["args"]["definition"] == {
        "to_source": {
            "relationship_type": "object",
            "source": "external_pg",
            "table": {"schema": "public", "name": "product_catalog"},
            "field_mapping": {"sku": "sku"},
        }
    }
    context.engine_profile = "unknown"
    assert (
        "UNSUPPORTED_ENGINE_PROFILE"
        in relationship_step(compile_manifest(parse_manifest(json.dumps(value)), context)).blockers
    )


def test_missing_target_is_unknown_not_fake_registered():
    value = json.loads((EXAMPLES / "sales-cross-source.json").read_text())
    plan = compile_manifest(parse_manifest(json.dumps(value)))
    assert relationship_step(plan).blockers == ("TARGET_UNKNOWN",)


def test_source_and_product_approval_block_dependents():
    context = context_for()
    approve_view_uniqueness(context)
    context.approved_sources = frozenset()
    plan = compile_manifest(parse_manifest(json.dumps(warehouse())), context)
    assert all(step.action == "BLOCKED" for step in plan.steps if step.layer == "HASURA")
    assert not plan.ready_for_dispatch


def test_alias_customization_only_declares_public_names_not_permissions():
    value = warehouse()
    value["data_products"][1]["fields"][1]["alias"] = "quantity"
    context = context_for()
    context.metadata.metadata["sources"][0]["tables"] = []
    plan = compile_manifest(parse_manifest(json.dumps(value)), context)
    track = next(step for step in plan.steps if step.key == "track:warehouse.available_inventory")
    assert track.payload["args"]["configuration"] == {
        "column_config": {"available_quantity": {"custom_name": "quantity"}}
    }
    assert track.action == "CREATE"
    assert not any("permission" in step.payload.get("type", "") for step in plan.steps)


def test_relationship_name_conflicts_with_existing_other_kind():
    context = context_for()
    approve_view_uniqueness(context)
    context.metadata.metadata["sources"][0]["tables"][1]["array_relationships"] = [
        {
            "name": "product",
            "using": {},
        }
    ]
    plan = compile_manifest(parse_manifest(json.dumps(warehouse())), context)
    assert "RELATIONSHIP_NAME_CONFLICT" in relationship_step(plan).blockers


def test_untracked_remote_target_is_blocked_even_if_core_says_published():
    context = context_for()
    approve_view_uniqueness(context)
    context.metadata.metadata["sources"][0]["tables"] = [
        context.metadata.metadata["sources"][0]["tables"][1]
    ]
    plan = compile_manifest(parse_manifest(json.dumps(warehouse())), context)
    assert "TARGET_NOT_TRACKED" in relationship_step(plan).blockers


def test_it_can_configure_a_verified_target_without_a_consumer_read_grant():
    context = context_for()
    approve_view_uniqueness(context)
    plan = compile_manifest(parse_manifest(json.dumps(warehouse())), context)
    assert "TARGET_ACCESS_REQUIRED" not in relationship_step(plan).blockers
    assert relationship_step(plan).action == "CREATE"


def test_same_version_contract_draft_is_editable_but_published_is_not():
    value = warehouse()
    context = context_for(value)
    original = compile_manifest(parse_manifest(json.dumps(value)), context)
    context.existing_core = {
        step.key: fingerprint(step.payload) for step in original.steps if step.layer == "CORE"
    }
    key = next(step.key for step in original.steps if step.key.startswith("contract:"))
    value["data_products"][0]["fields"][0]["description"] = "Revised draft description"
    context.contract_states[key] = "DRAFT"
    edited = compile_manifest(parse_manifest(json.dumps(value)), context)
    assert next(step.action for step in edited.steps if step.key == key) == "UPDATE"
    context.contract_states[key] = "PUBLISHED"
    immutable = compile_manifest(parse_manifest(json.dumps(value)), context)
    assert next(step.action for step in immutable.steps if step.key == key) == "BLOCKED"
