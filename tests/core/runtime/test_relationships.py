import asyncio
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.core.data_products.querying import (
    QueryLimits,
    QuerySelection,
    query_shape,
)
from app.core.data_sources.discovery import SourceSnapshot
from app.core.db.commands import timestamp
from app.core.db.ports import StoredResource
from app.core.hasura_provisioner.client import table_in
from app.core.hasura_provisioner.relationships import HasuraRelationships, RelationshipMetadata
from app.core.hasura_provisioner.schemas import PostgresConnection, SelectPermission, TableRef
from app.core.hasura_provisioner.worker import LeaseLost, PublicationWorker
from app.platform.http.errors import CoreError, ErrorCode

from .fakes import SALES, WAREHOUSE, granted

pytestmark = pytest.mark.asyncio


class RelationshipEngine:
    def __init__(self, original):
        self.original = original
        self.nested_rows = []
        self.requests = []
        self.on_read = None
        self.partial_error = False
        self.timeout_after_commit = False
        self.version_conflict_once = False

    def __call__(self, request):
        if request.url.path not in {"/v1/graphql", "/v1/metadata"}:
            return self.original.request(request)
        payload = json.loads(request.content) if request.content else {}
        if request.url.path == "/v1/graphql" and "CoreRelationshipRead" in payload.get("query", ""):
            assert "x-hasura-admin-secret" not in request.headers
            token = request.headers["Authorization"].removeprefix("Bearer ")
            context = self.original.credentials.contexts[token]
            assert str(context.department_id) == SALES
            self.requests.append(payload)
            if self.on_read:
                self.on_read()
            namespace = self.original.metadata["sources"][0]["name"]
            result = {"data": {namespace: {"rows": deepcopy(self.nested_rows)}}}
            if self.partial_error:
                result["errors"] = [{"message": "SYNTHETIC_PRIVATE_FAILURE"}]
            return httpx.Response(200, json=result)
        operation = payload.get("type", "")
        creates = {
            "pg_create_object_relationship": "object_relationships",
            "pg_create_array_relationship": "array_relationships",
            "pg_create_remote_relationship": "remote_relationships",
        }
        if operation not in {*creates, "pg_drop_relationship", "pg_delete_remote_relationship"}:
            return self.original.request(request)
        assert payload["resource_version"] == self.original.version
        if self.version_conflict_once:
            self.version_conflict_once = False
            self.original.version += 1
            return httpx.Response(409, json={"code": "conflict"})
        args = payload["args"]
        source = next(
            item for item in self.original.metadata["sources"] if item["name"] == args["source"]
        )
        table = next(item for item in source["tables"] if item["table"] == args["table"])
        self.original.mutations.append(operation)
        if operation in creates:
            definition = {
                key: deepcopy(value)
                for key, value in args.items()
                if key not in {"source", "table"}
            }
            table.setdefault(creates[operation], []).append(definition)
        else:
            assert not args.get("cascade")
            for collection in (
                "object_relationships",
                "array_relationships",
                "remote_relationships",
            ):
                table[collection] = [
                    item
                    for item in table.get(collection, [])
                    if item["name"] != args.get("relationship", args.get("name"))
                ]
        self.original.version += 1
        if self.timeout_after_commit:
            self.timeout_after_commit = False
            raise httpx.ReadTimeout("SYNTHETIC_COMMITTED_LOST_REPLY")
        return httpx.Response(200, json={"message": "success"})


async def graph(harness):
    _, source, product, contract, grant = await granted(harness)
    await harness.worker.run_once("worker")
    source = harness.record("source", source["id"])
    product = harness.record("product", product["id"])
    contract = harness.record("contract", contract["id"])
    engine = RelationshipEngine(harness.engine)
    harness.http._transport = httpx.MockTransport(engine)
    endpoints = [(source, product, contract, harness.record("grant", grant["id"]))]
    for index, remote in enumerate((False, True, True), 1):
        new_source = deepcopy(source)
        if remote:
            new_source.update(
                id=str(uuid4()),
                hasura_source_name=f"test_source_{index}",
                source_key=f"test.source{index}",
            )
            original = harness.store.data["source"][source["id"]]
            harness.store.data["source"][new_source["id"]] = StoredResource(
                new_source, original.private
            )
            await harness.metadata.add_postgres_source(
                new_source["hasura_source_name"],
                PostgresConnection(
                    environment_name="WAREHOUSE_URL",
                    database_url="postgresql://reader:SYNTHETIC@warehouse.example.test/fixture?sslmode=require",
                ),
            )
        new_product, new_contract, new_grant = (
            deepcopy(product),
            deepcopy(contract),
            deepcopy(grant),
        )
        new_product.update(
            id=str(uuid4()),
            data_source_id=new_source["id"],
            product_key=f"test.product{index}",
            object_name=f"table_{index}",
            status="ACTIVE",
        )
        new_contract.update(id=str(uuid4()), data_product_id=new_product["id"])
        new_product["active_contract_id"] = new_contract["id"]
        new_grant.update(
            id=str(uuid4()),
            data_product_id=new_product["id"],
            contract_version_id=new_contract["id"],
            status="ACTIVE",
            applied_revision=1,
        )
        for kind, item in (
            ("product", new_product),
            ("contract", new_contract),
            ("grant", new_grant),
        ):
            harness.store.data[kind][item["id"]] = StoredResource(item, {})
        table = TableRef(schema="public", name=new_product["object_name"])
        await harness.metadata.track_table(new_source["hasura_source_name"], table)
        await harness.metadata.configure_read_mapping(
            new_source["hasura_source_name"],
            table,
            {"sku": "sku", "available_quantity": "available"},
        )
        await harness.metadata.create_select_permission(
            new_source["hasura_source_name"],
            table,
            UUID(SALES),
            SelectPermission(columns=("sku", "available_quantity")),
        )
        endpoints.append((new_source, new_product, new_contract, new_grant))
    base = (
        await harness.app.state.core.discovery.snapshot(harness.store.data["source"][source["id"]])
    ).objects[0]

    async def snapshot(record):
        objects = [
            base.model_copy(
                update={"table": TableRef(schema="public", name=item[1]["object_name"])}
            )
            for item in endpoints
            if item[0]["id"] == record.public["id"]
        ]
        return SourceSnapshot(
            source_key=record.public["source_key"], schemas=("public",), objects=tuple(objects)
        )

    harness.app.state.core.discovery = SimpleNamespace(
        snapshot=snapshot, policy=harness.app.state.core.discovery.policy
    )
    return endpoints, engine


async def relationship(harness, source, target, *, name="related", kind="object", publish=True):
    response = await harness.request(
        "POST",
        "/relationships",
        json={
            "name": name,
            "source_product_id": source[1]["id"],
            "target_product_id": target[1]["id"],
            "relationship_type": kind,
            "field_mapping": {"sku": "sku"},
        },
    )
    assert response.status_code == 201, response.text
    record = response.json()
    if publish:
        response = await harness.request(
            "POST",
            f"/relationships/{record['id']}/publish",
            json={"expected_revision": record["revision"]},
        )
        assert response.status_code == 202, response.text
        await harness.worker.run_once("worker")
        assert harness.record("job", response.json()["job_id"])["status"] == "SUCCEEDED"
        record = harness.record("relationship", record["id"])
    return record


async def query(harness, root, body, **kwargs):
    return await harness.request(
        "POST", f"/data-products/{root[1]['id']}/query", actor="consumer", json=body, **kwargs
    )


async def test_registry_local_remote_lifecycle_idempotency_and_no_implicit_grants(harness):
    endpoints, engine = await graph(harness)
    before = len(harness.store.data["grant"])
    for target in endpoints[1:]:
        record = await relationship(
            harness, endpoints[0], target, name="related_" + target[1]["object_name"]
        )
        assert record["status"] == "ACTIVE" and record["applied_revision"] == record["revision"]
        get = await harness.request("GET", "/relationships/" + record["id"])
        assert get.json() == record
        assert (
            await harness.request("GET", "/relationships/" + record["id"], actor="consumer")
        ).status_code == 403
        assert (
            await harness.request(
                "PATCH",
                "/relationships/" + record["id"],
                json={"expected_revision": record["revision"], "field_mapping": {"sku": "sku"}},
            )
        ).status_code == 409
        response = await harness.request(
            "POST",
            "/relationships/" + record["id"] + "/disable",
            json={"expected_revision": record["revision"], "reason": "Stop sharing relationship"},
        )
        assert response.status_code == 202
        await harness.worker.run_once("worker")
        assert harness.record("relationship", record["id"])["status"] == "DISABLED"
        patched = await harness.request(
            "PATCH",
            "/relationships/" + record["id"],
            json={
                "expected_revision": harness.record("relationship", record["id"])["revision"],
                "field_mapping": {"sku": "sku"},
            },
        )
        assert patched.status_code == 200
    assert len(harness.store.data["grant"]) == before
    assert "pg_create_object_relationship" in harness.engine.mutations
    assert "pg_create_remote_relationship" in harness.engine.mutations


async def test_three_sources_four_products_nested_query_and_fresh_projection(harness):
    endpoints, engine = await graph(harness)
    for index, name in enumerate(("lines", "product", "supplier")):
        await relationship(
            harness,
            endpoints[index],
            endpoints[index + 1],
            name=name,
            kind="array" if index == 0 else "object",
        )
    body = {
        "select": ["sku"],
        "limit": 1,
        "include": {
            "lines": {
                "select": ["sku"],
                "limit": 2,
                "include": {
                    "product": {
                        "select": ["available"],
                        "include": {"supplier": {"select": ["sku"]}},
                    }
                },
            }
        },
    }
    engine.nested_rows = [
        {
            "sku": "ORDER-1",
            "lines": [
                {"sku": "SKU-1", "product": {"available": 9, "supplier": {"sku": "SUPPLIER-1"}}}
            ],
        }
    ]
    response = await query(harness, endpoints[0], body)
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == engine.nested_rows
    assert len(engine.requests) == 1
    assert "lines(" in engine.requests[0]["query"] and "supplier{" in engine.requests[0]["query"]
    assert not any(value in engine.requests[0]["query"] for value in ("SKU-1", "ORDER-1"))
    engine.nested_rows[0]["lines"][0]["product"]["available"] = 11
    assert (await query(harness, endpoints[0], body)).json()["rows"] == engine.nested_rows
    engine.nested_rows = [{"sku": "ORDER-1"}]
    response = await query(harness, endpoints[0], {"select": ["sku"]})
    assert response.status_code == 200 and "lines" not in engine.requests[-1]["query"]
    details = harness.store.audits[-1][-1]["details"]
    assert details["duration_ms"] >= 0


@pytest.mark.parametrize(
    "failure",
    [
        "missing_grant",
        "revoked",
        "expired",
        "columns",
        "membership",
        "different_department",
        "contract",
        "relationship",
        "scope",
        "schema",
        "metadata",
    ],
)
async def test_fail_closed_on_every_selected_node(harness, failure):
    endpoints, engine = await graph(harness)
    record = await relationship(harness, endpoints[0], endpoints[2])
    target_grant = harness.record("grant", endpoints[2][3]["id"])
    if failure == "missing_grant":
        del harness.store.data["grant"][target_grant["id"]]
    elif failure == "revoked":
        target_grant["status"] = "REVOKING"
    elif failure == "expired":
        target_grant["expires_at"] = timestamp(harness.store.clock - timedelta(seconds=1))
    elif failure == "columns":
        target_grant["scope_json"]["columns"] = ["available_quantity"]
    elif failure == "membership":
        harness.store.principals["consumer"]["memberships"][0]["status"] = "REVOKED"
    elif failure == "different_department":
        target_grant["consumer_department_id"] = WAREHOUSE
        harness.store.principals["consumer"]["memberships"].append(
            harness.store.membership(WAREHOUSE)
        )
    elif failure == "contract":
        harness.record("product", endpoints[2][1]["id"])["status"] = "DISABLED"
    elif failure == "relationship":
        harness.record("relationship", record["id"])["revision"] += 1
    elif failure == "scope":
        target_grant["scope_json"]["row_scope"] = {"kind": "FILTER"}
    elif failure == "schema":
        original = harness.app.state.core.discovery.snapshot

        async def snapshot(source):
            value = await original(source)
            return value.model_copy(update={"objects": ()})

        harness.app.state.core.discovery.snapshot = snapshot
    elif failure == "metadata":
        source = harness.engine.metadata["sources"][0]
        table_in(source, TableRef(schema="public", name=endpoints[0][1]["object_name"]))[
            "remote_relationships"
        ][0]["definition"]["to_source"]["field_mapping"] = {"sku": "secret"}
    response = await query(
        harness,
        endpoints[0],
        {"select": ["sku"], "include": {"related": {"select": ["available"]}}},
        headers={"X-Hasura-Role": "admin", "X-Department-Id": SALES},
    )
    assert response.status_code in {403, 409, 422, 503}, response.text
    assert not engine.requests


async def test_revoke_during_read_and_partial_errors_never_return_data(harness):
    endpoints, engine = await graph(harness)
    await relationship(harness, endpoints[0], endpoints[2])
    body = {"select": ["sku"], "include": {"related": {"select": ["sku"]}}}
    engine.nested_rows = [{"sku": "ONE", "related": None}]
    response = await query(harness, endpoints[0], body)
    assert response.status_code == 200 and response.json()["rows"][0]["related"] is None
    engine.partial_error = True
    response = await query(harness, endpoints[0], body)
    assert response.status_code == 503 and "ONE" not in response.text
    engine.partial_error = False

    def revoke():
        harness.record("grant", endpoints[2][3]["id"])["status"] = "REVOKING"

    engine.on_read = revoke
    response = await query(harness, endpoints[0], body)
    assert response.status_code == 403 and "ONE" not in response.text


async def test_cycle_metadata_allowed_but_query_depth_and_unsupported_operators_rejected(harness):
    endpoints, engine = await graph(harness)
    await relationship(harness, endpoints[0], endpoints[0], name="self_ref")
    body = {"select": ["sku"]}
    for _ in range(5):
        body = {"select": ["sku"], "include": {"self_ref": body}}
    assert (await query(harness, endpoints[0], body)).status_code == 422
    for bad in (
        {"select": ["sku"], "where": {"self_ref": {"_eq": "x"}}},
        {"select": ["sku"], "where": {"sku": {"_like": "%"}}},
        {"select": ["sku"], "include": {"self_ref": {"select": ["sku"], "limit": 1}}},
        {"select": ["sku", "sku"]},
    ):
        assert (await query(harness, endpoints[0], bad)).status_code == 422
    assert not engine.requests


async def test_filter_values_are_variables_and_ordering_columns_require_grant(harness):
    endpoints, engine = await graph(harness)
    engine.nested_rows = [{"sku": "SKU-1"}]
    value = 'SKU-1") { secret } #'
    response = await query(
        harness,
        endpoints[0],
        {
            "select": ["sku"],
            "where": {"sku": {"_eq": value}},
            "order_by": [{"field": "available", "direction": "desc"}],
        },
    )
    assert response.status_code == 200, response.text
    assert value not in engine.requests[0]["query"]
    assert any(
        isinstance(item, dict) and item.get("sku") == {"_eq": value}
        for item in engine.requests[0]["variables"].values()
    )
    harness.record("grant", endpoints[0][3]["id"])["scope_json"]["columns"] = ["sku"]
    response = await query(
        harness, endpoints[0], {"select": ["sku"], "order_by": [{"field": "available"}]}
    )
    assert response.status_code == 403 and len(engine.requests) == 1


async def test_publish_retry_lost_reply_readback_and_metadata_conflict(harness):
    endpoints, engine = await graph(harness)
    record = await relationship(harness, endpoints[0], endpoints[2], publish=False)
    engine.timeout_after_commit = True
    response = await harness.request(
        "POST",
        f"/relationships/{record['id']}/publish",
        json={"expected_revision": record["revision"]},
        key="same-publish-key",
    )
    replay = await harness.request(
        "POST",
        f"/relationships/{record['id']}/publish",
        json={"expected_revision": record["revision"]},
        key="same-publish-key",
    )
    assert replay.json() == response.json()
    await harness.worker.run_once("first-worker")
    job = harness.record("job", response.json()["job_id"])
    assert job["status"] == "RETRY"
    harness.store.clock += timedelta(seconds=10)
    await harness.worker.run_once("restart-worker")
    assert harness.record("job", job["id"])["status"] == "SUCCEEDED"
    assert harness.engine.mutations.count("pg_create_remote_relationship") == 1
    assert harness.record("relationship", record["id"])["status"] == "ACTIVE"


async def test_composite_mapping_unique_proof_and_duplicate_target_rejected(harness):
    endpoints, _ = await graph(harness)
    body = {
        "name": "composite",
        "source_product_id": endpoints[0][1]["id"],
        "target_product_id": endpoints[2][1]["id"],
        "relationship_type": "object",
        "field_mapping": {"sku": "sku", "available_quantity": "available_quantity"},
    }
    assert (await harness.request("POST", "/relationships", json=body)).status_code == 201
    original = harness.app.state.core.discovery.snapshot

    async def duplicate_keys(source):
        value = await original(source)
        return value.model_copy(
            update={
                "objects": tuple(
                    item.model_copy(update={"primary_key": (), "unique_constraints": ()})
                    for item in value.objects
                )
            }
        )

    harness.app.state.core.discovery.snapshot = duplicate_keys
    body["name"] = "not_unique"
    assert (await harness.request("POST", "/relationships", json=body)).status_code == 503
    with pytest.raises(ValidationError):
        QuerySelection.model_validate({"select": ["__schema"]})


async def test_metadata_adapter_never_overwrites_foreign_definition(harness):
    endpoints, _ = await graph(harness)
    record = await relationship(harness, endpoints[0], endpoints[2])
    relation = RelationshipMetadata(
        endpoints[0][0]["hasura_source_name"],
        TableRef(schema="public", name=endpoints[0][1]["object_name"]),
        endpoints[2][0]["hasura_source_name"],
        TableRef(schema="public", name=endpoints[2][1]["object_name"]),
        record["name"],
        "object",
        {"sku": "available_quantity"},
    )
    before = deepcopy(harness.engine.metadata)
    from app.core.hasura_provisioner.errors import MetadataConflict

    with pytest.raises(MetadataConflict):
        await HasuraRelationships(harness.metadata).apply(relation)
    assert harness.engine.metadata == before


async def test_query_complexity_and_body_limits(harness):
    endpoints, engine = await graph(harness)
    service = harness.app.state.relationship_queries
    service.limits = QueryLimits(max_complexity=1, max_request_bytes=1024)
    assert (await query(harness, endpoints[0], {"select": ["sku"]})).status_code == 422
    with pytest.raises(CoreError) as error:
        query_shape(QuerySelection(select=("sku",), limit=100), QueryLimits(max_rows=5))
    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert not engine.requests


async def test_manifest_examples_and_recursive_wire_contract():
    from jsonschema import Draft202012Validator
    from tests.contracts.test_api_contracts import DOCUMENTS, bundle

    from app.core.applications.runtime import OnboardingSubmission
    from app.core.data_products.relationships import RelationshipDefinition

    root = Path(__file__).resolve().parents[3]
    example = json.loads((root / "contracts/fixtures/relationship-onboarding.json").read_text())
    for submission in example["submissions"].values():
        OnboardingSubmission.model_validate(submission)
    for definition in example["relationships"]:
        RelationshipDefinition.model_validate(definition)
    QuerySelection.model_validate(example["query"])
    uri = (root / "contracts/relationships.yaml").as_uri()
    schema = bundle(DOCUMENTS[uri]["components"]["schemas"]["RelationshipQuerySelection"], uri)
    validator = Draft202012Validator(schema)
    validator.validate(example["query"])
    bad = deepcopy(example["query"])
    bad["include"]["lines"]["include"]["product"]["raw_sql"] = "SELECT secret"
    assert not validator.is_valid(bad)


async def test_concurrent_publish_claim_and_restart_fencing(harness):
    endpoints, _ = await graph(harness)
    record = await relationship(harness, endpoints[0], endpoints[2], publish=False)
    receipt = await harness.request(
        "POST", f"/relationships/{record['id']}/publish", json={"expected_revision": 1}
    )
    first = await harness.worker.claim("old-worker")
    harness.store.clock += timedelta(seconds=31)
    replacement = PublicationWorker(harness.app.state.core, harness.metadata, harness.settings)
    second = await replacement.claim("replacement-worker")
    assert first.job_id == second.job_id and second.attempt == first.attempt + 1
    with pytest.raises(LeaseLost):
        await harness.worker.execute(first)
    assert "pg_create_remote_relationship" not in harness.engine.mutations
    await replacement.execute(second)
    assert harness.record("relationship", record["id"])["status"] == "ACTIVE"
    assert harness.record("job", receipt.json()["job_id"])["attempt_count"] == 2


async def test_two_workers_publish_once_and_queued_disable_supersedes_publish(harness):
    endpoints, _ = await graph(harness)
    record = await relationship(harness, endpoints[0], endpoints[2], publish=False)
    receipt = await harness.request(
        "POST", f"/relationships/{record['id']}/publish", json={"expected_revision": 1}
    )
    other = PublicationWorker(harness.app.state.core, harness.metadata, harness.settings)
    outcomes = await asyncio.gather(harness.worker.run_once("first"), other.run_once("second"))
    assert sorted(outcomes) == [False, True]
    assert harness.record("job", receipt.json()["job_id"])["status"] == "SUCCEEDED"
    assert harness.engine.mutations.count("pg_create_remote_relationship") == 1
    draft = await relationship(harness, endpoints[1], endpoints[3], name="canceled", publish=False)
    publish = await harness.request(
        "POST", f"/relationships/{draft['id']}/publish", json={"expected_revision": 1}
    )
    disable = await harness.request(
        "POST",
        f"/relationships/{draft['id']}/disable",
        json={"expected_revision": 2, "reason": "Cancel queued publication"},
    )
    await harness.worker.run_once("first")
    await harness.worker.run_once("second")
    assert harness.record("job", publish.json()["job_id"])["status"] == "FAILED"
    assert harness.record("job", disable.json()["job_id"])["status"] == "SUCCEEDED"
    assert harness.record("relationship", draft["id"])["status"] == "DISABLED"
    assert harness.engine.mutations.count("pg_create_remote_relationship") == 1


async def test_remote_array_multiple_branches_and_nonunique_mapping(harness):
    endpoints, engine = await graph(harness)
    await relationship(harness, endpoints[0], endpoints[2], name="remote_rows", kind="array")
    await relationship(harness, endpoints[0], endpoints[1], name="local_object")
    engine.nested_rows = [{"sku": "ONE", "remote_rows": [], "local_object": None}]
    response = await query(
        harness,
        endpoints[0],
        {
            "select": ["sku"],
            "limit": 1,
            "include": {
                "remote_rows": {
                    "select": ["available"],
                    "limit": 2,
                    "where": {"available": {"_gt": 0}},
                },
                "local_object": {"select": ["sku"]},
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == engine.nested_rows
    assert "remote_rows(" in engine.requests[-1]["query"]
    response = await harness.request(
        "POST",
        "/relationships",
        json={
            "name": "no_unique_target",
            "source_product_id": endpoints[0][1]["id"],
            "target_product_id": endpoints[2][1]["id"],
            "relationship_type": "object",
            "field_mapping": {"available_quantity": "available_quantity"},
        },
    )
    assert response.status_code == 422


async def test_unrelated_metadata_cas_conflict_is_retried_without_replacement(harness):
    endpoints, engine = await graph(harness)
    record = await relationship(harness, endpoints[0], endpoints[2], publish=False)
    receipt = await harness.request(
        "POST", f"/relationships/{record['id']}/publish", json={"expected_revision": 1}
    )
    engine.version_conflict_once = True
    await harness.worker.run_once("worker-one")
    assert harness.record("job", receipt.json()["job_id"])["status"] == "RETRY"
    harness.store.clock += timedelta(seconds=10)
    await harness.worker.run_once("worker-two")
    assert harness.record("job", receipt.json()["job_id"])["status"] == "SUCCEEDED"
    assert harness.engine.mutations.count("pg_create_remote_relationship") == 1


async def test_metadata_version_change_during_query_discards_response(harness):
    endpoints, engine = await graph(harness)
    engine.nested_rows = [{"sku": "DO-NOT-RETURN"}]

    def drift():
        harness.engine.version += 1

    engine.on_read = drift
    response = await query(harness, endpoints[0], {"select": ["sku"]})
    assert response.status_code == 409 and "DO-NOT-RETURN" not in response.text


@pytest.mark.parametrize("failure", [None, "revoked", "metadata", "duplicate"])
async def test_bounded_scan_authorizes_the_entire_response_and_rejects_drift(harness, failure):
    endpoints, engine = await graph(harness)
    root = endpoints[0]
    values = [{"sku": f"SKU-{i:03}"} for i in range(205)]

    def page():
        variables = engine.requests[-1]["variables"]
        offset, limit = variables["v1"], variables["v0"]
        engine.nested_rows = deepcopy(values[offset : offset + limit])
        if offset == 100:
            if failure == "revoked":
                harness.record("grant", root[3]["id"])["status"] = "REVOKING"
            elif failure == "metadata":
                harness.engine.version += 1
            elif failure == "duplicate":
                engine.nested_rows[0] = deepcopy(values[0])

    engine.on_read = page
    response = await harness.request(
        "POST",
        f"/data-products/{root[1]['id']}/scan",
        actor="consumer",
        json={"selection": {"select": ["sku"], "limit": 100}, "pages": 4},
    )
    if failure is None:
        assert response.status_code == 200, response.text
        assert response.json()["rows"] == values
        assert response.json()["has_more"] is False
        assert len(engine.requests) == 3
    else:
        assert response.status_code == (403 if failure == "revoked" else 409), response.text
        assert "SKU-000" not in response.text
    assert harness.store.audits[-1][-1]["details"]["scan_pages"] == 4


@pytest.mark.parametrize(
    "extra",
    [
        {"pages": 5},
        {"pages": True},
        {"selection": {"select": ["sku"], "offset": 9900, "limit": 100}},
        {"selection": {"select": ["available"], "limit": 100}},
        {"selection": {"select": ["sku"], "include": {"related": {"select": ["sku"]}}}},
    ],
)
async def test_scan_never_expands_page_depth_or_unbounded_offsets(harness, extra):
    endpoints, engine = await graph(harness)
    response = await harness.request(
        "POST",
        f"/data-products/{endpoints[0][1]['id']}/scan",
        actor="consumer",
        json={"selection": {"select": ["sku"], "limit": 100}, "pages": 4, **extra},
    )
    assert response.status_code == 422, response.text
    assert not engine.requests
