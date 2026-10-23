from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from app.core.hasura_provisioner import (
    DataProductPublisher,
    HasuraPermissionService,
    SourceRegistrationService,
)
from app.core.hasura_provisioner.adapters import EnvironmentSecretResolver
from app.core.hasura_provisioner.errors import (
    ContractRejected,
    HasuraUnavailable,
    InvalidConfiguration,
    InvalidMetadata,
    ProvisioningDenied,
)
from app.core.hasura_provisioner.schemas import PermissionTarget, SelectGrant
from app.platform.identity.authentication import VerifiedIdentity

from .fakes import CONTRACT_ID, DEPARTMENT_ID, PRODUCT_ID, SOURCE_ID

pytestmark = pytest.mark.asyncio


def registrar(harness, **kwargs):
    return SourceRegistrationService(
        harness.client, harness.sources, harness.secrets, harness.settings, **kwargs
    )


def publisher(harness, **kwargs):
    return DataProductPublisher(
        harness.client, harness.sources, harness.products, harness.contracts, **kwargs
    )


def permissions(harness, **kwargs):
    return HasuraPermissionService(
        harness.client,
        harness.sources,
        harness.products,
        harness.grants,
        harness.contracts,
        harness.settings,
        **kwargs,
    )


def grant(**kwargs):
    values = dict(
        id=UUID(int=5),
        product_id=PRODUCT_ID,
        contract_id=CONTRACT_ID,
        department_id=DEPARTMENT_ID,
        columns=("sku", "available_quantity"),
        row_scope="ALL",
        approved=True,
        status="PENDING_APPLY",
        revision=1,
    )
    values.update(kwargs)
    return SelectGrant(**values)


def target():
    return PermissionTarget(product_id=PRODUCT_ID, department_id=DEPARTMENT_ID)


async def prepare(harness):
    await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
    await publisher(harness, identity=harness.identity).publish(PRODUCT_ID, harness.actor)


async def test_register_and_publish_only_requested_view(harness):
    await prepare(harness)
    assert harness.sources.connected == [SOURCE_ID]
    assert harness.products.active == [PRODUCT_ID]
    assert len(harness.engine.source()["tables"]) == 1
    assert harness.engine.table()["table"]["name"] == "stock_available"
    assert "select_permissions" not in harness.engine.table()


async def test_deny_without_identity_provider(harness):
    with pytest.raises(ProvisioningDenied):
        await registrar(harness).register(SOURCE_ID, harness.actor)
    with pytest.raises(ProvisioningDenied):
        await publisher(harness).publish(PRODUCT_ID, harness.actor)
    with pytest.raises(ProvisioningDenied):
        await permissions(harness).reconcile(target(), harness.actor)
    assert not harness.engine.requests


async def test_deny_non_admin(harness):
    actor = VerifiedIdentity(issuer=harness.actor.issuer, subject="staff")
    with pytest.raises(ProvisioningDenied):
        await registrar(harness, identity=harness.identity).register(SOURCE_ID, actor)
    assert not harness.engine.requests


@pytest.mark.parametrize("field", ["allowed_sources", "allowed_database_hosts"])
async def test_source_allowlists(harness, field):
    setattr(harness.settings, field, frozenset())
    with pytest.raises(InvalidConfiguration):
        await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
    assert not harness.sources.connected
    assert not harness.engine.requests


async def test_source_requires_approval(harness):
    harness.sources.record = harness.sources.record.model_copy(update={"approved": False})
    with pytest.raises(ProvisioningDenied):
        await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
    assert not harness.engine.requests


@pytest.mark.parametrize(
    "dsn",
    [
        "mysql://user:pass@host/db?sslmode=require",
        "postgresql://user:pass@host/db",
        "postgresql://host/db?sslmode=require",
        "postgresql://user:pass@host:99999/db?sslmode=require",
        "postgresql://user:pass@host/db?sslmode=disable",
        "postgresql://user:pass@host/db?sslmode=require&sslmode=disable",
        "postgresql://user:pass@host/db?sslmode=require&host=other-host",
        "postgresql://user:pass@host/db?sslmode=require&host=",
        "postgresql://user:pass@host/db?sslmode=require&options=-csearch_path=public",
        "postgresql://user:pass@host/db?sslmode=require&connect_timeout=not-a-number",
    ],
)
async def test_invalid_source_configuration(dsn):
    resolver = EnvironmentSecretResolver({"reader": "DB_URL"}, {"DB_URL": dsn})
    with pytest.raises(InvalidConfiguration) as caught:
        await resolver.resolve_postgres("reader")
    assert "pass" not in str(caught.value)


async def test_unknown_secret_reference():
    with pytest.raises(InvalidConfiguration):
        await EnvironmentSecretResolver({}, {}).resolve_postgres("anything")


async def test_failed_source_never_connected(harness):
    harness.engine.failures["pg_add_source"] = httpx.Response(503)
    with pytest.raises(HasuraUnavailable):
        await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
    assert not harness.sources.connected


@pytest.mark.parametrize("failure", ["contract", "hasura", "inconsistent", "unapproved"])
async def test_failed_publication_never_active(harness, failure):
    await registrar(harness, identity=harness.identity).register(SOURCE_ID, harness.actor)
    if failure == "contract":
        harness.contracts.reject = True
    elif failure == "hasura":
        harness.engine.failures["pg_track_table"] = httpx.Response(503)
    elif failure == "inconsistent":
        harness.engine.consistent = False
    else:
        harness.products.record = harness.products.record.model_copy(update={"approved": False})
    with pytest.raises((ContractRejected, HasuraUnavailable, InvalidMetadata, ProvisioningDenied)):
        await publisher(harness, identity=harness.identity).publish(PRODUCT_ID, harness.actor)
    assert not harness.products.active


async def test_permission_builder_and_deny_logistics(harness):
    await prepare(harness)
    harness.grants.records = [grant()]
    await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    configured = harness.engine.table()["select_permissions"]
    assert len(configured) == 1
    assert configured[0]["role"] == f"dxos_department_{DEPARTMENT_ID.hex}"
    assert configured[0]["permission"]["columns"] == ["available_quantity", "sku"]
    assert configured[0]["permission"]["filter"] == {}
    assert not configured[0]["permission"]["allow_aggregations"]
    assert harness.grants.applied == [(UUID(int=5), 1)]
    assert {request["type"] for request in harness.engine.writes()} <= {
        "pg_add_source",
        "pg_track_table",
        "pg_create_select_permission",
        "pg_drop_select_permission",
    }


async def test_revoke_preserves_remaining_grants(harness):
    await prepare(harness)
    harness.grants.records = [grant(), grant(id=UUID(int=6), columns=("sku",))]
    service = permissions(harness, identity=harness.identity)
    await service.reconcile(target(), harness.actor)
    harness.grants.records[0] = grant(status="REVOKING", revision=2)
    await service.revoke(target(), UUID(int=5), harness.actor)
    configured = harness.engine.table()["select_permissions"]
    assert configured[0]["permission"]["columns"] == ["sku"]
    assert harness.grants.revoked == [(UUID(int=5), 2)]
    harness.grants.records[1] = grant(id=UUID(int=6), columns=("sku",), status="REVOKING")
    await service.revoke(target(), UUID(int=6), harness.actor)
    assert harness.engine.table()["select_permissions"] == []


async def test_revoke_requires_desired_state_recorded(harness):
    harness.grants.records = [grant()]
    with pytest.raises(ProvisioningDenied):
        await permissions(harness, identity=harness.identity).revoke(
            target(), UUID(int=5), harness.actor
        )
    assert not harness.engine.requests


async def test_expired_grant_removed(harness):
    await prepare(harness)
    harness.grants.records = [grant()]
    service = permissions(harness, identity=harness.identity)
    await service.reconcile(target(), harness.actor)
    harness.grants.records = [grant(expires_at=datetime.now(UTC) - timedelta(seconds=1))]
    await service.reconcile(target(), harness.actor)
    assert harness.engine.table()["select_permissions"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"columns": ("cost_price",)},
        {"approved": False},
        {"department_id": UUID(int=99)},
    ],
)
async def test_invalid_grant_is_not_applied(harness, changes):
    await prepare(harness)
    harness.grants.records = [grant(**changes)]
    with pytest.raises((ContractRejected, ProvisioningDenied)):
        await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    assert not harness.grants.applied
    assert not harness.engine.table().get("select_permissions")


@pytest.mark.parametrize(
    "changes",
    [
        {"role": "admin"},
        {"enforcement_role": "admin"},
        {"row_scope": "CUSTOM"},
        {"columns": ("*",)},
        {"expires_at": datetime.now()},
    ],
)
async def test_untrusted_grant_fields_rejected(changes):
    with pytest.raises(ValidationError):
        grant(**changes)


async def test_failed_permission_not_applied(harness):
    await prepare(harness)
    harness.grants.records = [grant()]
    harness.engine.failures["pg_create_select_permission"] = httpx.Response(503)
    with pytest.raises(HasuraUnavailable):
        await permissions(harness, identity=harness.identity).reconcile(target(), harness.actor)
    assert not harness.grants.applied


async def test_replacement_failure_does_not_restore_revoked_columns(harness):
    await prepare(harness)
    harness.grants.records = [grant(), grant(id=UUID(int=6), columns=("sku",))]
    service = permissions(harness, identity=harness.identity)
    await service.reconcile(target(), harness.actor)
    harness.grants.records[0] = grant(status="REVOKING", revision=2)
    harness.engine.failures["pg_create_select_permission"] = httpx.Response(503)
    with pytest.raises(HasuraUnavailable):
        await service.revoke(target(), UUID(int=5), harness.actor)
    assert harness.engine.table()["select_permissions"] == []
    assert not harness.grants.revoked


async def test_revoke_after_product_disabled_or_contract_unavailable(harness):
    await prepare(harness)
    harness.grants.records = [grant()]
    service = permissions(harness, identity=harness.identity)
    await service.reconcile(target(), harness.actor)
    harness.products.record = harness.products.record.model_copy(update={"approved": False})
    harness.sources.record = harness.sources.record.model_copy(update={"approved": False})
    harness.contracts.reject = True
    harness.grants.records = [grant(status="REVOKING", revision=2)]
    await service.revoke(target(), UUID(int=5), harness.actor)
    assert not harness.engine.table()["select_permissions"]
    assert harness.grants.revoked == [(UUID(int=5), 2)]


async def test_contract_drift_removes_old_broader_permission(harness):
    await prepare(harness)
    harness.grants.records = [grant()]
    service = permissions(harness, identity=harness.identity)
    await service.reconcile(target(), harness.actor)
    harness.contracts.reject = True
    with pytest.raises(ContractRejected):
        await service.reconcile(target(), harness.actor)
    assert not harness.engine.table()["select_permissions"]


async def test_failed_revoke_is_not_acknowledged(harness):
    await prepare(harness)
    harness.grants.records = [grant()]
    service = permissions(harness, identity=harness.identity)
    await service.reconcile(target(), harness.actor)
    harness.grants.records = [grant(status="REVOKING", revision=2)]
    harness.engine.failures["pg_drop_select_permission"] = httpx.Response(503)
    with pytest.raises(HasuraUnavailable):
        await service.revoke(target(), UUID(int=5), harness.actor)
    assert harness.engine.table()["select_permissions"]
    assert not harness.grants.revoked
