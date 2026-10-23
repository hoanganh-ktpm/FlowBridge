from datetime import UTC, datetime
from uuid import UUID

from app.platform.identity.authentication import VerifiedIdentity

from .client import HasuraMetadataClient
from .errors import ContractRejected, InvalidConfiguration, MetadataConflict, ProvisioningDenied
from .ports import (
    AccessGrantRepository,
    DataContractValidator,
    DataProductRepository,
    DataSourceRepository,
    IdentityResolver,
    SecretResolver,
)
from .schemas import (
    PermissionTarget,
    ProductRecord,
    SelectGrant,
    SelectPermission,
    SourceRecord,
    ValidatedContract,
)
from .settings import HasuraSettings


async def require_admin(
    identity: IdentityResolver | None, actor: VerifiedIdentity, action: str
) -> None:
    if identity is None:
        raise ProvisioningDenied()
    await identity.require_admin(actor, action)


def validate_contract(
    product: ProductRecord, contract: ValidatedContract, expected_id: UUID | None = None
) -> None:
    if (
        contract.product_id != product.id
        or contract.contract_id != (expected_id or product.contract_id)
        or contract.status != "PUBLISHED"
    ):
        raise ContractRejected()


def check_revision(actual: int, expected: int | None) -> None:
    if expected is not None:
        if type(expected) is not int or expected < 1:
            raise InvalidConfiguration()
        if actual != expected:
            raise MetadataConflict()


class SourceRegistrationService:
    def __init__(
        self,
        client: HasuraMetadataClient,
        sources: DataSourceRepository,
        secrets: SecretResolver,
        settings: HasuraSettings,
        identity: IdentityResolver | None = None,
    ) -> None:
        self._client = client
        self._sources = sources
        self._secrets = secrets
        self._settings = settings
        self._identity = identity

    async def register(
        self, source_id: UUID, actor: VerifiedIdentity, *, expected_revision: int | None = None
    ) -> None:
        await require_admin(self._identity, actor, "hasura.source.register")
        source = await self._sources.get(source_id)
        check_revision(source.revision, expected_revision)
        if source.id != source_id or not source.approved or source.status == "DISABLED":
            raise ProvisioningDenied()
        if source.name not in self._settings.allowed_sources:
            raise InvalidConfiguration()
        connection = await self._secrets.resolve_postgres(source.secret_ref)
        if (
            connection.hostname not in self._settings.allowed_database_hosts
            or not source.matches_connection(connection)
        ):
            raise InvalidConfiguration()
        await self._client.add_postgres_source(source.name, connection)
        await self._client.require_consistent()
        await self._sources.mark_connected(source.id, source.revision)


class DataProductPublisher:
    def __init__(
        self,
        client: HasuraMetadataClient,
        sources: DataSourceRepository,
        products: DataProductRepository,
        contracts: DataContractValidator,
        identity: IdentityResolver | None = None,
    ) -> None:
        self._client = client
        self._sources = sources
        self._products = products
        self._contracts = contracts
        self._identity = identity

    async def publish(
        self, product_id: UUID, actor: VerifiedIdentity, *, expected_revision: int | None = None
    ) -> None:
        await require_admin(self._identity, actor, "hasura.product.publish")
        product = await self._products.get(product_id)
        check_revision(product.revision, expected_revision)
        source = await self._sources.get(product.source_id)
        if (
            product.id != product_id
            or source.id != product.source_id
            or not product.approved
            or not source.approved
            or source.status != "CONNECTED"
            or product.status not in {"READY", "PROVISIONING", "ACTIVE", "ERROR"}
        ):
            raise ProvisioningDenied()
        validate_contract(product, await self._contracts.validate(source, product))
        await self._client.track_table(source.name, product.table)
        await self._client.require_consistent()
        await self._products.mark_active(product.id, product.revision)


class HasuraPermissionService:
    def __init__(
        self,
        client: HasuraMetadataClient,
        sources: DataSourceRepository,
        products: DataProductRepository,
        grants: AccessGrantRepository,
        contracts: DataContractValidator,
        settings: HasuraSettings,
        identity: IdentityResolver | None = None,
    ) -> None:
        self._client = client
        self._sources = sources
        self._products = products
        self._grants = grants
        self._contracts = contracts
        self._settings = settings
        self._identity = identity

    async def _target(self, target: PermissionTarget) -> tuple[SourceRecord, ProductRecord]:
        product = await self._products.get(target.product_id)
        source = await self._sources.get(product.source_id)
        if product.id != target.product_id or source.id != product.source_id:
            raise ProvisioningDenied()
        return source, product

    async def reconcile(
        self,
        target: PermissionTarget,
        actor: VerifiedIdentity,
        *,
        expected_grant_id: UUID | None = None,
        expected_revision: int | None = None,
    ) -> None:
        await require_admin(self._identity, actor, "hasura.permission.reconcile")
        if (expected_grant_id is None) != (expected_revision is None):
            raise InvalidConfiguration()
        await self._reconcile(
            target, expected_grant_id=expected_grant_id, expected_revision=expected_revision
        )

    async def _effective_grants(
        self, source: SourceRecord, product: ProductRecord, candidates: list[SelectGrant]
    ) -> tuple[list[SelectGrant], list[SelectGrant]]:
        effective = []
        contracts: dict[UUID, ValidatedContract | None] = {}
        invalid = []
        for grant in candidates:
            if not grant.approved:
                invalid.append(grant)
                continue
            if grant.contract_id not in contracts:
                try:
                    contract = await self._contracts.validate(
                        source, product, contract_id=grant.contract_id
                    )
                    validate_contract(product, contract, grant.contract_id)
                except ContractRejected:
                    contracts[grant.contract_id] = None
                else:
                    contracts[grant.contract_id] = contract
            contract = contracts[grant.contract_id]
            if contract is None or not set(grant.columns) <= set(contract.columns):
                invalid.append(grant)
                continue
            effective.append(grant)
        return effective, invalid

    async def _reconcile(
        self,
        target: PermissionTarget,
        revoke_id: UUID | None = None,
        *,
        expected_grant_id: UUID | None = None,
        expected_revision: int | None = None,
    ) -> None:
        async with self._grants.lock(target):
            source, product = await self._target(target)
            grants = await self._grants.list_for_target(target)
            if expected_grant_id is not None:
                expected = next((grant for grant in grants if grant.id == expected_grant_id), None)
                if expected is None:
                    raise MetadataConflict()
                check_revision(expected.desired_revision, expected_revision)
            if revoke_id is not None:
                grant = next((grant for grant in grants if grant.id == revoke_id), None)
                if grant is None or grant.status not in {"REVOKING", "REVOKED", "EXPIRED"}:
                    raise ProvisioningDenied()
            now = datetime.now(UTC)
            candidates = []
            revoked = []
            for grant in grants:
                if (
                    grant.product_id != target.product_id
                    or grant.department_id != target.department_id
                ):
                    raise ProvisioningDenied()
                if grant.status in {"REVOKING", "REVOKED", "EXPIRED"} or (
                    grant.expires_at is not None and grant.expires_at <= now
                ):
                    if (
                        grant.status not in {"REVOKED", "EXPIRED"}
                        or grant.applied_revision != grant.desired_revision
                    ):
                        revoked.append(grant)
                    continue
                candidates.append(grant)
            if candidates:
                if (
                    not source.approved
                    or not product.approved
                    or source.status != "CONNECTED"
                    or product.status != "ACTIVE"
                ):
                    await self._client.drop_select_permission(
                        source.name, product.table, target.department_id
                    )
                    raise ProvisioningDenied()
            effective, invalid = await self._effective_grants(source, product, candidates)
            columns = sorted({column for grant in effective for column in grant.columns})
            if columns:
                await self._client.create_select_permission(
                    source.name,
                    product.table,
                    target.department_id,
                    SelectPermission(columns=tuple(columns), limit=self._settings.select_limit),
                )
            else:
                await self._client.drop_select_permission(
                    source.name, product.table, target.department_id
                )
            await self._client.require_consistent()
            for grant in effective:
                if grant.expires_at is not None and grant.expires_at <= datetime.now(UTC):
                    raise MetadataConflict()
                await self._grants.mark_applied(grant.id, grant.desired_revision)
            for grant in revoked:
                await self._grants.mark_revoked(grant.id, grant.desired_revision)
            if invalid:
                for grant in invalid:
                    await self._grants.mark_failed(
                        grant.id, grant.desired_revision, ContractRejected.code
                    )
                raise ContractRejected()

    async def revoke(
        self,
        target: PermissionTarget,
        grant_id: UUID,
        actor: VerifiedIdentity,
        *,
        expected_revision: int | None = None,
    ) -> None:
        await require_admin(self._identity, actor, "hasura.permission.revoke")
        await self._reconcile(
            target, grant_id, expected_grant_id=grant_id, expected_revision=expected_revision
        )
