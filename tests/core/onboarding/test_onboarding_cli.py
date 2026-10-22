import json
from contextlib import asynccontextmanager
from uuid import UUID

import pytest

from app.core.applications.cli import main
from app.core.applications.manifest import fingerprint, parse_manifest
from app.core.applications.onboarding import (
    ApplicationOnboardingService,
    DraftReceipt,
    MissingIntegration,
)
from app.core.data_sources.discovery import (
    DiscoveryError,
    PostgresSchemaDiscovery,
    SourceSnapshot,
)
from app.core.hasura_provisioner.adapters import EnvironmentSecretResolver
from app.core.hasura_provisioner.errors import InvalidConfiguration, ProvisioningDenied
from app.platform.identity.authentication import VerifiedIdentity

from .test_manifest import EXAMPLES, warehouse
from .test_planning import context_for, discovery_value


class DiscoveryFixture:
    def __init__(self):
        self.calls = 0
        self.failure = None

    async def discover(self, source):
        self.calls += 1
        if self.failure:
            raise self.failure
        return SourceSnapshot.model_validate(discovery_value())


class CoreFixture:
    durable = False

    def __init__(self):
        self.imports = {}

    async def plan_context(self, application_key, actor):
        return context_for()

    async def save_drafts(self, manifest, plan, owner_id, actor):
        assert owner_id == UUID(int=1)
        assert plan.manifest_hash == fingerprint(manifest.model_dump(mode="json", by_alias=True))
        self.imports.setdefault(plan.manifest_hash, len(self.imports) + 1)
        return DraftReceipt(
            application_key=manifest.application.key,
            revision=self.imports[plan.manifest_hash],
            state="WAITING_APPROVAL",
            durable=False,
        )


class TrustedIdentityFixture:
    async def require_admin(self, actor, action):
        if actor.subject != "test-admin":
            raise ProvisioningDenied()


class OwnershipFixture:
    async def require_owner(self, actor, application_key, department_key):
        assert department_key == "warehouse"
        return UUID(int=1)


@pytest.mark.asyncio
async def test_draft_import_idempotent_drift_and_no_fake_apply():
    discovery = DiscoveryFixture()
    repository = CoreFixture()
    service = ApplicationOnboardingService(
        discovery, repository, TrustedIdentityFixture(), OwnershipFixture()
    )
    actor = VerifiedIdentity(issuer="https://identity.example.invalid", subject="test-admin")
    manifest = parse_manifest(json.dumps(warehouse()))
    first = await service.import_drafts(manifest, actor)
    second = await service.import_drafts(manifest, actor)
    assert first == second
    assert first.state == "WAITING_APPROVAL" and first.durable is False
    drift = warehouse()
    drift["application"]["description"] = "New description"
    assert (await service.import_drafts(parse_manifest(json.dumps(drift)), actor)).revision == 2
    with pytest.raises(MissingIntegration):
        await service.apply(manifest, actor)


@pytest.mark.asyncio
async def test_unauthorized_actor_never_discovers_or_persists():
    discovery = DiscoveryFixture()
    repository = CoreFixture()
    actor = VerifiedIdentity(issuer="https://identity.example.invalid", subject="staff")
    service = ApplicationOnboardingService(
        discovery, repository, TrustedIdentityFixture(), OwnershipFixture()
    )
    with pytest.raises(ProvisioningDenied):
        await service.import_drafts(parse_manifest(json.dumps(warehouse())), actor)
    assert not discovery.calls and not repository.imports


@pytest.mark.asyncio
async def test_discovery_failure_does_not_persist_partial_drafts():
    discovery = DiscoveryFixture()
    discovery.failure = DiscoveryError()
    repository = CoreFixture()
    service = ApplicationOnboardingService(
        discovery, repository, TrustedIdentityFixture(), OwnershipFixture()
    )
    actor = VerifiedIdentity(issuer="https://identity.example.invalid", subject="test-admin")
    with pytest.raises(DiscoveryError):
        await service.import_drafts(parse_manifest(json.dumps(warehouse())), actor)
    assert not repository.imports


@pytest.mark.parametrize("command", ["validate", "plan"])
def test_cli_offline_commands(command, capsys):
    args = ["onboard", command, "--config", str(EXAMPLES / "warehouse.json"), "--json"]
    if command == "plan":
        args += ["--snapshot", str(EXAMPLES / "warehouse-discovery.json")]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    if command == "validate":
        assert result["valid"] is True
    else:
        assert result["evidence"] == "OFFLINE_SNAPSHOT"
        assert all(step["action"] == "BLOCKED" for step in result["steps"])


@pytest.mark.parametrize("command", ["apply", "status"])
def test_cli_missing_real_adapters_fail_closed(command, capsys):
    args = ["onboard", command, "--json"]
    args += ["--application", "warehouse"] if command == "status" else ["--config", "unused.json"]
    assert main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is False
    assert "adapters" in result["error"]


@pytest.mark.asyncio
async def test_missing_identity_provider_rejects_before_discovery():
    discovery = DiscoveryFixture()
    actor = VerifiedIdentity(issuer="https://identity.example.invalid", subject="test-admin")
    service = ApplicationOnboardingService(discovery, CoreFixture())
    with pytest.raises(ProvisioningDenied):
        await service.import_drafts(parse_manifest(json.dumps(warehouse())), actor)
    assert not discovery.calls


@pytest.mark.asyncio
async def test_service_plan_is_readonly():
    repository = CoreFixture()
    service = ApplicationOnboardingService(
        DiscoveryFixture(), repository, TrustedIdentityFixture(), OwnershipFixture()
    )
    actor = VerifiedIdentity(issuer="https://identity.example.invalid", subject="test-admin")
    plan = await service.plan(parse_manifest(json.dumps(warehouse())), actor)
    assert plan.valid
    assert not repository.imports


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_readonly_discovery_runs_only_static_session_commands(monkeypatch, fails):
    from app.core.applications.onboarding import discovery_source
    from app.core.data_sources import discovery as module

    statements = []
    disposed = []
    captured = {}
    source = discovery_source(
        parse_manifest(json.dumps(warehouse())), parse_manifest(json.dumps(warehouse())).sources[0]
    )

    class Database:
        async def execute(self, statement):
            statements.append(str(statement))

        async def run_sync(self, callback):
            if fails:
                raise TimeoutError("postgresql://synthetic-secret@private-host/database")
            return SourceSnapshot.model_validate(discovery_value())

    class Engine:
        @asynccontextmanager
        async def connect(self):
            yield Database()

        async def dispose(self):
            disposed.append(True)

    def create_engine(uri, **kwargs):
        captured.update(kwargs)
        return Engine()

    monkeypatch.setattr(module, "create_async_engine", create_engine)
    resolver = EnvironmentSecretResolver(
        {"warehouse-reader": "WAREHOUSE_URL"},
        {
            "WAREHOUSE_URL": "postgresql://dxos_reader:synthetic@warehouse.example.invalid/warehouse_db?sslmode=verify-full"
        },
    )
    adapter = PostgresSchemaDiscovery(resolver, frozenset({"warehouse.example.invalid"}))
    if fails:
        with pytest.raises(DiscoveryError) as caught:
            await adapter.discover(source)
        assert "synthetic-secret" not in str(caught.value)
        assert "private-host" not in str(caught.value)
    else:
        snapshot = await adapter.discover(source)
        assert snapshot.source_key == "warehouse-pg"
    assert statements == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '5s'"]
    assert disposed == [True]
    assert captured["hide_parameters"] is True
    assert captured["connect_args"]["ssl"].check_hostname is True


@pytest.mark.asyncio
async def test_readonly_discovery_disallows_unapproved_host():
    from app.core.applications.onboarding import discovery_source

    manifest = parse_manifest(json.dumps(warehouse()))
    resolver = EnvironmentSecretResolver(
        {"warehouse-reader": "WAREHOUSE_URL"},
        {
            "WAREHOUSE_URL": "postgresql://dxos_reader:synthetic@warehouse.example.invalid/warehouse_db?sslmode=verify-full"
        },
    )
    with pytest.raises(InvalidConfiguration):
        await PostgresSchemaDiscovery(resolver, frozenset()).discover(
            discovery_source(manifest, manifest.sources[0])
        )
