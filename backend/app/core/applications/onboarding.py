from typing import Protocol
from uuid import UUID

from pydantic import Field

from app.core.data_sources.discovery import DiscoverySource, SchemaDiscovery
from app.core.hasura_provisioner.errors import ProvisioningDenied
from app.core.hasura_provisioner.ports import IdentityResolver
from app.core.hasura_provisioner.schemas import DTO, TableRef
from app.platform.identity.authentication import VerifiedIdentity

from .manifest import ApplicationManifest, Source
from .planning import OnboardingPlan, PlanContext, compile_manifest


class MissingIntegration(ValueError):
    pass


class DraftReceipt(DTO):
    application_key: str
    revision: int = Field(ge=1)
    state: str
    durable: bool


class AffiliationResolver(Protocol):
    async def resolve_department(self, department_key: str) -> UUID: ...


class OnboardingRepository(Protocol):
    durable: bool

    async def plan_context(self, application_key: str, actor: VerifiedIdentity) -> PlanContext: ...

    async def save_drafts(
        self,
        manifest: ApplicationManifest,
        plan: OnboardingPlan,
        department_id: UUID,
        actor: VerifiedIdentity,
    ) -> DraftReceipt: ...

    async def status(self, application_key: str, actor: VerifiedIdentity) -> DraftReceipt: ...


class ApprovedPlanDispatcher(Protocol):
    durable: bool

    async def enqueue(
        self,
        manifest: ApplicationManifest,
        plan: OnboardingPlan,
        department_id: UUID,
        actor: VerifiedIdentity,
    ) -> DraftReceipt: ...


def discovery_source(manifest: ApplicationManifest, source: Source) -> DiscoverySource:
    return DiscoverySource(
        key=source.key,
        host=source.host,
        port=source.port,
        database=source.database,
        username=source.username,
        ssl_mode=source.ssl_mode,
        secret_ref=source.secret_ref,
        objects=tuple(
            TableRef(schema=product.object.schema_name, name=product.object.name)
            for product in manifest.data_products
            if product.source_key == source.key
        ),
    )


class ApplicationOnboardingService:
    def __init__(
        self,
        discovery: SchemaDiscovery,
        repository: OnboardingRepository | None = None,
        identity: IdentityResolver | None = None,
        affiliation: AffiliationResolver | None = None,
        dispatcher: ApprovedPlanDispatcher | None = None,
    ) -> None:
        self._discovery = discovery
        self._repository = repository
        self._identity = identity
        self._affiliation = affiliation
        self._dispatcher = dispatcher

    async def _authorize(self, manifest: ApplicationManifest, actor: VerifiedIdentity) -> UUID:
        if self._identity is None or self._affiliation is None:
            raise ProvisioningDenied()
        await self._identity.require_admin(actor, "application.onboard")
        return await self._affiliation.resolve_department(manifest.application.department_key)

    async def _prepare(
        self, manifest: ApplicationManifest, actor: VerifiedIdentity
    ) -> OnboardingPlan:
        if self._repository is None:
            raise MissingIntegration("Core repository adapter is not configured")
        context = await self._repository.plan_context(manifest.application.key, actor)
        context.admin_verified = True
        for source in manifest.sources:
            context.snapshots[source.key] = await self._discovery.discover(
                discovery_source(manifest, source)
            )
        return compile_manifest(manifest, context)

    async def plan(self, manifest: ApplicationManifest, actor: VerifiedIdentity) -> OnboardingPlan:
        await self._authorize(manifest, actor)
        return await self._prepare(manifest, actor)

    async def import_drafts(
        self, manifest: ApplicationManifest, actor: VerifiedIdentity
    ) -> DraftReceipt:
        department = await self._authorize(manifest, actor)
        plan = await self._prepare(manifest, actor)
        if not plan.valid:
            raise ValueError("Manifest does not match physical schema; review plan issues")
        receipt = await self._repository.save_drafts(manifest, plan, department, actor)
        if receipt.state not in {"DRAFT", "WAITING_APPROVAL"}:
            raise ValueError("Draft import must not publish or grant access")
        if receipt.durable != self._repository.durable:
            raise ValueError("Persistence acknowledgement mismatch")
        return receipt

    async def apply(self, manifest: ApplicationManifest, actor: VerifiedIdentity) -> DraftReceipt:
        department = await self._authorize(manifest, actor)
        if (
            self._repository is None
            or not self._repository.durable
            or self._dispatcher is None
            or not self._dispatcher.durable
        ):
            raise MissingIntegration(
                "Apply requires durable Core, authorization and publication job adapters"
            )
        plan = await self._prepare(manifest, actor)
        if not plan.ready_for_dispatch:
            raise ProvisioningDenied()
        receipt = await self._dispatcher.enqueue(manifest, plan, department, actor)
        if not receipt.durable or receipt.state != "QUEUED":
            raise ValueError("Publication can only acknowledge a durable QUEUED job, never ACTIVE")
        return receipt
