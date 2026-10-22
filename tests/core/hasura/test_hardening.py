import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from app.core.hasura_provisioner import HasuraMetadataClient
from app.core.hasura_provisioner.errors import (
    ContractRejected,
    HasuraTimeout,
    InvalidConfiguration,
    MetadataConflict,
    ProvisioningDenied,
)
from app.core.hasura_provisioner.schemas import (
    ProductRecord,
    PublicationJob,
    SelectGrant,
    SelectPermission,
    SourceRecord,
)

from .fakes import CONTRACT_ID, DEPARTMENT_ID, PRODUCT_ID, SOURCE_ID
from .test_services import grant, permissions, prepare, publisher, registrar, target

pytestmark = pytest.mark.asyncio


async def test_invalid_grant_does_not_remove_another_valid_grant(harness):
    await prepare(harness)
    harness.grants.records = [grant(), grant(id=UUID(int=6), columns=("sku",))]
    service = permissions(harness, identity=harness.identity)
    await service.reconcile(target(), harness.actor)
    harness.grants.records[0] = grant(approved=False)
    with pytest.raises(ContractRejected):
        await service.reconcile(target(), harness.actor)
    configured = harness.engine.table()["select_permissions"]
    assert len(configured) == 1
    assert configured[0]["permission"]["columns"] == ["sku"]
    assert harness.grants.records[0].status == "ERROR"
    assert harness.grants.records[1].status == "ACTIVE"


async def test_approved_but_unpublished_product_cannot_receive_permissions(harness):
    await prepare(harness)
    harness.products.record = harness.products.record.model_copy(update={"status": "READY"})
    harness.grants.records = [grant()]
    with pytest.raises(ProvisioningDenied):
        await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    assert not harness.engine.table().get("select_permissions")


async def test_replacement_does_not_delete_concurrently_changed_permission(harness):
    await prepare(harness)
    table = harness.products.record.table
    await harness.client.create_select_permission(
        "warehouse", table, DEPARTMENT_ID, SelectPermission(columns=("sku", "available_quantity"))
    )
    changed = False

    def upstream(request):
        nonlocal changed
        payload = json.loads(request.content)
        response = harness.engine(request)
        if payload["type"] == "export_metadata" and not changed:
            harness.engine.table()["select_permissions"][0]["permission"]["filter"] = {
                "sku": {"_eq": "restricted"}
            }
            harness.engine.version += 1
            changed = True
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        client = HasuraMetadataClient(harness.settings, http)
        with pytest.raises(MetadataConflict):
            await client.create_select_permission(
                "warehouse", table, DEPARTMENT_ID, SelectPermission(columns=("sku",))
            )
    assert harness.engine.table()["select_permissions"][0]["permission"]["filter"] == {
        "sku": {"_eq": "restricted"}
    }


async def test_grant_projection_distinguishes_desired_and_applied_revisions():
    assert {"desired_revision", "applied_revision"} <= SelectGrant.model_fields.keys()


@pytest.mark.parametrize(
    "changes",
    [
        {"database_name": "different_database"},
        {"db_user": "different_user"},
        {"port": 6543},
        {"ssl_mode": "verify-full"},
        {"host": "different.example.invalid"},
    ],
)
async def test_source_dsn_must_match_core_configuration(harness, changes):
    harness.sources.record = harness.sources.record.model_copy(update=changes)
    with pytest.raises(InvalidConfiguration):
        await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
    assert not harness.engine.requests
    assert not harness.sources.connected


async def test_disabled_source_cannot_be_registered(harness):
    harness.sources.record = harness.sources.record.model_copy(update={"status": "DISABLED"})
    with pytest.raises(ProvisioningDenied):
        await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
    assert not harness.engine.requests


@pytest.mark.parametrize("status", ["DRAFT", "RETIRED"])
async def test_contract_must_be_published(harness, status):
    await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
    harness.contracts.status = status
    with pytest.raises(ContractRejected):
        await publisher(harness, identity=harness.identity).publish(PRODUCT_ID, harness.actor)
    assert not harness.products.active
    assert not harness.engine.source()["tables"]


async def test_grants_keep_their_pinned_contract_version(harness):
    await prepare(harness)
    old_contract_id = UUID(int=90)
    harness.grants.records = [grant(contract_id=old_contract_id)]
    await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    assert harness.contracts.validated_ids[-1] == old_contract_id
    assert harness.products.record.contract_id == CONTRACT_ID
    assert harness.grants.records[0].status == "ACTIVE"


async def test_invalid_pinned_contract_does_not_remove_valid_grant(harness):
    await prepare(harness)
    old_contract_id = UUID(int=90)
    harness.grants.records = [
        grant(contract_id=old_contract_id),
        grant(id=UUID(int=6), columns=("sku",)),
    ]
    service = permissions(harness, identity=harness.identity)
    await service.reconcile(target(), harness.actor)
    harness.contracts.rejected_contracts.add(old_contract_id)
    with pytest.raises(ContractRejected):
        await service.reconcile(target(), harness.actor)
    assert harness.engine.table()["select_permissions"][0]["permission"]["columns"] == ["sku"]


async def test_core_v02_projection_fixture(harness):
    fixture = json.loads((Path(__file__).parent / "fixtures/core-v02.json").read_text())
    source = SourceRecord.model_validate(fixture["source"])
    product = ProductRecord.model_validate(fixture["product"])
    selected = SelectGrant.model_validate(fixture["grant"])
    assert source.name == "warehouse"
    assert product.source_id == SOURCE_ID
    assert product.contract_id == CONTRACT_ID
    assert selected.revision == selected.desired_revision == 3
    assert selected.applied_revision == 2
    assert selected.columns == ("sku", "available_quantity")
    assert selected.enforcement_role == f"dxos_department_{DEPARTMENT_ID.hex}"
    await prepare(harness)
    harness.grants.records = [selected]
    await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    assert harness.grants.records[0].applied_revision == 3
    assert harness.grants.records[0].status == "ACTIVE"


@pytest.mark.parametrize(
    "changes",
    [
        {"desired_revision": 1, "applied_revision": 2},
        {"status": "ACTIVE", "desired_revision": 3, "applied_revision": 2},
        {"consumer_principal_id": UUID(int=99)},
        {"enforcement_role": "admin"},
        {"scope_json": {"action": "UPDATE", "columns": ["sku"], "row_scope": {"kind": "ALL"}}},
        {"scope_json": {"action": "SELECT", "columns": ["sku"], "row_scope": {"kind": "CUSTOM"}}},
    ],
)
async def test_core_projection_rejects_unsupported_or_escalating_state(changes):
    values = json.loads((Path(__file__).parent / "fixtures/core-v02.json").read_text())["grant"]
    values.update(changes)
    with pytest.raises(ValidationError):
        SelectGrant.model_validate(values)


async def test_stale_source_job_has_no_external_effect(harness):
    with pytest.raises(MetadataConflict):
        await registrar(harness, identity=harness.identity).register(
            SOURCE_ID, harness.actor, expected_revision=2
        )
    assert not harness.engine.requests


async def test_stale_product_job_has_no_external_effect(harness):
    await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
    count = len(harness.engine.requests)
    with pytest.raises(MetadataConflict):
        await publisher(harness, identity=harness.identity).publish(
            PRODUCT_ID, harness.actor, expected_revision=2
        )
    assert len(harness.engine.requests) == count


async def test_stale_grant_job_has_no_external_effect(harness):
    await prepare(harness)
    harness.grants.records = [grant(revision=2, status="REVOKING")]
    count = len(harness.engine.requests)
    with pytest.raises(MetadataConflict):
        await permissions(harness, identity=harness.identity).reconcile(
            target(), harness.actor, expected_grant_id=UUID(int=5), expected_revision=1
        )
    assert len(harness.engine.requests) == count
    assert not harness.grants.applied


@pytest.mark.parametrize(
    "operation",
    [
        "pg_add_source",
        "pg_track_table",
        "pg_create_select_permission",
        "pg_drop_select_permission",
    ],
)
async def test_retry_after_timeout_with_already_committed_side_effect(harness, operation):
    if operation != "pg_add_source":
        await prepare(harness)
    harness.grants.records = [grant()]
    service = permissions(harness, identity=harness.identity)
    if operation == "pg_drop_select_permission":
        await service.reconcile(target(), harness.actor)
        harness.grants.records = [grant(status="REVOKING", revision=2)]
    harness.engine.timeout_after_apply.add(operation)

    async def execute():
        if operation == "pg_add_source":
            await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
        elif operation == "pg_track_table":
            await publisher(harness, identity=harness.identity).publish(PRODUCT_ID, harness.actor)
        else:
            await service.reconcile(target(), harness.actor)

    if operation == "pg_track_table":
        harness.engine.source()["tables"] = []
        harness.products.active.clear()
    with pytest.raises(HasuraTimeout):
        await execute()
    if operation == "pg_add_source":
        assert not harness.sources.connected
    elif operation == "pg_track_table":
        assert not harness.products.active
    elif operation == "pg_create_select_permission":
        assert not harness.grants.applied
    else:
        assert not harness.grants.revoked
    count = len(harness.engine.writes())
    await execute()
    assert len(harness.engine.writes()) == count
    if operation == "pg_drop_select_permission":
        assert not harness.engine.table()["select_permissions"]
        assert harness.grants.revoked == [(UUID(int=5), 2)]


async def test_revision_changed_during_http_cannot_be_acknowledged(harness):
    await prepare(harness)
    harness.grants.records = [grant()]
    original = harness.client.create_select_permission

    async def apply_then_revoke(*args, **kwargs):
        await original(*args, **kwargs)
        harness.grants.records = [grant(status="REVOKING", revision=2)]

    harness.client.create_select_permission = apply_then_revoke
    with pytest.raises(MetadataConflict):
        await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    assert not harness.grants.applied
    await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    assert not harness.engine.table()["select_permissions"]


async def test_cancelled_request_does_not_acknowledge_source(harness):
    async def cancelled(request):
        raise asyncio.CancelledError()

    async with httpx.AsyncClient(transport=httpx.MockTransport(cancelled)) as http:
        harness.client = HasuraMetadataClient(harness.settings, http)
        with pytest.raises(asyncio.CancelledError):
            await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
        assert not http.is_closed
    assert not harness.sources.connected


@pytest.mark.parametrize(
    "changes",
    [
        {"data_product_id": PRODUCT_ID},
        {"job_type": "CONNECT_SOURCE"},
        {"target_revision": 0},
        {"attempt_count": 6},
        {"status": "RUNNING"},
        {"next_attempt_at": datetime.now()},
    ],
)
async def test_job_projection_enforces_target_revision_and_lease(changes):
    values = dict(
        id=UUID(int=20),
        job_type="APPLY_GRANT",
        access_grant_id=UUID(int=5),
        target_revision=3,
        idempotency_key="APPLY_GRANT:5:3",
    )
    values.update(changes)
    with pytest.raises(ValidationError):
        PublicationJob(**values)


async def test_core_unique_live_grant_with_terminal_history(harness):
    await prepare(harness)
    harness.grants.records = [
        grant(status="REVOKED", revision=2, applied_revision=2),
        grant(id=UUID(int=6), columns=("sku",)),
    ]
    await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    assert harness.engine.table()["select_permissions"][0]["permission"]["columns"] == ["sku"]
    assert not harness.grants.revoked


async def test_same_contract_is_validated_once_per_reconcile(harness):
    await prepare(harness)
    harness.contracts.validated_ids.clear()
    harness.grants.records = [grant(), grant(id=UUID(int=6), columns=("sku",))]
    await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    assert harness.contracts.validated_ids == [CONTRACT_ID]


async def test_expiry_during_apply_cannot_be_acknowledged(harness):
    await prepare(harness)
    expiry = datetime.now(UTC) + timedelta(hours=1)
    harness.grants.records = [grant(expires_at=expiry)]
    original = harness.client.create_select_permission

    async def apply_then_expire(*args, **kwargs):
        await original(*args, **kwargs)
        harness.grants.records = [grant(expires_at=datetime.now(UTC) - timedelta(seconds=1))]

    harness.client.create_select_permission = apply_then_expire
    with pytest.raises(MetadataConflict):
        await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    assert not harness.grants.applied


async def test_revoke_one_department_preserves_other_department_and_principal_role(harness):
    await prepare(harness)
    service = permissions(harness, identity=harness.identity)
    harness.grants.records = [grant()]
    await service.reconcile(target(), harness.actor)
    other_department = UUID(int=10)
    other_target = target().model_copy(update={"department_id": other_department})
    harness.grants.records = [grant(id=UUID(int=6), department_id=other_department)]
    await service.reconcile(other_target, harness.actor)
    principal_permission = {
        "role": "separately_managed_principal",
        "permission": {"columns": ["sku"], "filter": {}},
    }
    harness.engine.table()["select_permissions"].append(principal_permission)
    harness.grants.records = [grant(status="REVOKING", revision=2)]
    await service.revoke(target(), UUID(int=5), harness.actor)
    remaining = harness.engine.table()["select_permissions"]
    assert {permission["role"] for permission in remaining} == {
        f"dxos_department_{other_department.hex}",
        "separately_managed_principal",
    }
    assert principal_permission in remaining


@pytest.mark.parametrize("status", [200, 400])
async def test_malformed_error_code_is_typed_not_a_type_error(harness, status):
    harness.engine.failures["export_metadata"] = httpx.Response(
        status, json={"code": ["invalid"], "error": "private diagnostics"}
    )
    from app.core.hasura_provisioner.errors import HasuraRejected

    with pytest.raises(HasuraRejected):
        await harness.client.export_metadata()
