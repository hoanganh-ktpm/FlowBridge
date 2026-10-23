import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from app.core.data_sources.service import source_record
from app.core.db.commands import timestamp, uuid
from app.core.db.ports import StoredResource
from app.core.governance.policy import resolve_principal
from app.core.governance.service import instant, source_affiliation
from app.platform.http.errors import CoreError, ErrorCode

from .errors import HasuraError, HasuraUnavailable, MetadataConflict
from .schemas import PermissionTarget, ProductRecord, SelectGrant, TableRef, ValidatedContract
from .services import DataProductPublisher, HasuraPermissionService, SourceRegistrationService


class LeaseLost(Exception):
    pass


@dataclass(frozen=True)
class JobLease:
    job_id: UUID
    worker_id: str
    attempt: int


def target_of(job):
    kind = job["job_type"]
    if kind == "CONNECT_SOURCE":
        return "source", uuid(job["data_source_id"])
    if kind in {"PUBLISH_PRODUCT", "DISABLE_PRODUCT"}:
        return "product", uuid(job["data_product_id"])
    return "grant", uuid(job["access_grant_id"])


class PublicationWorker:
    def __init__(self, service, client, settings, *, lease_seconds: int = 30) -> None:
        if lease_seconds < 3:
            raise ValueError("Lease must allow renewal")
        self.service = service
        self.store = service.store
        self.client = client
        self.settings = settings
        self.lease_seconds = lease_seconds

    async def claim(self, worker_id: str) -> JobLease | None:
        if not worker_id or len(worker_id) > 100:
            raise ValueError("Worker identity required")
        async with self.store.transaction() as transaction:
            now = await transaction.now()
            jobs = await transaction.claimable_jobs(now, limit=10)
            for record in sorted(
                jobs, key=lambda item: (item.public["next_attempt_at"], item.public["id"])
            ):
                job = record.public
                due = (
                    job["status"] in {"QUEUED", "RETRY"} and instant(job["next_attempt_at"]) <= now
                )
                expired = (
                    job["status"] == "RUNNING"
                    and instant(record.private["lease_expires_at"]) <= now
                )
                if not due and not expired:
                    continue
                if job["attempt_count"] >= job["max_attempts"]:
                    kind, resource_id = target_of(job)
                    resource = await transaction.get(kind, resource_id)
                    revision = resource.public.get(
                        "desired_revision", resource.public.get("revision")
                    )
                    terminal = {
                        "source": ("VALIDATING", "FAILED"),
                        "product": ("PROVISIONING", "ERROR"),
                        "grant": ("PENDING_APPLY", "ERROR"),
                    }
                    before, after = terminal[kind]
                    if revision == job["target_revision"] and resource.public["status"] == before:
                        await transaction.replace(
                            kind,
                            StoredResource(
                                {**resource.public, "status": after, "updated_at": timestamp(now)},
                                resource.private,
                            ),
                        )
                    await transaction.replace(
                        "job",
                        StoredResource(
                            {
                                **job,
                                "status": "FAILED",
                                "last_error_code": "ATTEMPT_LIMIT",
                                "updated_at": timestamp(now),
                            },
                            {**record.private, "locked_by": None, "lease_expires_at": None},
                        ),
                    )
                    await transaction.audit(
                        None,
                        record.private["correlation_id"],
                        "publication.failed",
                        uuid(job["id"]),
                        outcome="FAILURE",
                        error_code="ATTEMPT_LIMIT",
                        initiator_id=uuid(record.private["requested_by"]),
                    )
                    continue
                attempt = job["attempt_count"] + 1
                await transaction.replace(
                    "job",
                    StoredResource(
                        {
                            **job,
                            "status": "RUNNING",
                            "attempt_count": attempt,
                            "updated_at": timestamp(now),
                        },
                        {
                            **record.private,
                            "locked_by": worker_id,
                            "lease_expires_at": timestamp(
                                now + timedelta(seconds=self.lease_seconds)
                            ),
                        },
                    ),
                )
                return JobLease(uuid(job["id"]), worker_id, attempt)
        return None

    @asynccontextmanager
    async def guard(self, lease: JobLease, *, target=True, actor=True):
        async with self.store.transaction() as transaction:
            now = await transaction.now()
            record = await transaction.get("job", lease.job_id)
            if (
                record.public["status"] != "RUNNING"
                or record.private["locked_by"] != lease.worker_id
                or record.public["attempt_count"] != lease.attempt
                or instant(record.private["lease_expires_at"]) <= now
            ):
                raise LeaseLost()
            if target:
                kind, resource_id = target_of(record.public)
                resource = await transaction.get(kind, resource_id)
                revision = resource.public.get("desired_revision", resource.public.get("revision"))
                if revision != record.public["target_revision"]:
                    raise CoreError(ErrorCode.VERSION_CONFLICT)
            if actor:
                identity = await transaction.identity_for_principal(
                    uuid(record.private["requested_by"])
                )
                principal = await resolve_principal(transaction, identity)
                principal.require_admin()
                if str(principal.principal_id) != record.private["requested_by"]:
                    raise CoreError(ErrorCode.FORBIDDEN)
            yield transaction, record, now

    async def renew(self, lease):
        async with self.guard(lease) as (transaction, record, now):
            await transaction.replace(
                "job",
                StoredResource(
                    record.public,
                    {
                        **record.private,
                        "lease_expires_at": timestamp(now + timedelta(seconds=self.lease_seconds)),
                    },
                ),
            )

    async def _heartbeat(self, lease):
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            await self.renew(lease)

    async def _with_heartbeat(self, lease):
        operation = asyncio.create_task(self.execute(lease))
        heartbeat = asyncio.create_task(self._heartbeat(lease))
        try:
            done, _ = await asyncio.wait(
                (operation, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                await heartbeat
            await operation
        finally:
            for task in (operation, heartbeat):
                task.cancel()
            await asyncio.gather(operation, heartbeat, return_exceptions=True)

    async def run_once(self, worker_id: str) -> bool:
        lease = await self.claim(worker_id)
        if lease is None:
            return False
        try:
            await self._with_heartbeat(lease)
            async with self.guard(lease) as (transaction, record, now):
                await transaction.replace(
                    "job",
                    StoredResource(
                        {
                            **record.public,
                            "status": "SUCCEEDED",
                            "last_error_code": None,
                            "updated_at": timestamp(now),
                        },
                        {**record.private, "locked_by": None, "lease_expires_at": None},
                    ),
                )
                await transaction.audit(
                    None,
                    record.private["correlation_id"],
                    "publication.succeeded",
                    lease.job_id,
                    initiator_id=uuid(record.private["requested_by"]),
                )
        except LeaseLost:
            pass
        except CoreError as error:
            code = (
                "STALE_REVISION" if error.code == ErrorCode.VERSION_CONFLICT else error.code.value
            )
            await self.fail(
                lease,
                code,
                retry=error.code in {ErrorCode.UPSTREAM_UNAVAILABLE, ErrorCode.UPSTREAM_TIMEOUT},
            )
        except HasuraError as error:
            await self.fail(lease, error.code, retry=isinstance(error, HasuraUnavailable))
        except Exception:
            await self.fail(lease, "WORKER_FAILURE", retry=False)
        return True

    async def fail(self, lease, code, *, retry):
        try:
            async with self.guard(lease, target=False, actor=False) as (transaction, record, now):
                kind, resource_id = target_of(record.public)
                resource = await transaction.get(kind, resource_id)
                revision = resource.public.get("desired_revision", resource.public.get("revision"))
                if revision != record.public["target_revision"]:
                    code, retry = "STALE_REVISION", False
                retry = retry and lease.attempt < record.public["max_attempts"]
                if not retry and revision == record.public["target_revision"]:
                    transitions = {
                        "source": ("VALIDATING", "FAILED"),
                        "product": ("PROVISIONING", "ERROR"),
                        "grant": ("PENDING_APPLY", "ERROR"),
                    }
                    before, after = transitions[kind]
                    if resource.public["status"] == before:
                        await transaction.replace(
                            kind,
                            StoredResource(
                                {**resource.public, "status": after, "updated_at": timestamp(now)},
                                resource.private,
                            ),
                        )
                await transaction.replace(
                    "job",
                    StoredResource(
                        {
                            **record.public,
                            "status": "RETRY" if retry else "FAILED",
                            "last_error_code": code,
                            "next_attempt_at": timestamp(
                                now
                                + timedelta(
                                    seconds=min(
                                        300,
                                        2**lease.attempt
                                        + ((lease.job_id.int ^ lease.attempt) % 1000) / 1000,
                                    )
                                )
                            ),
                            "updated_at": timestamp(now),
                        },
                        {**record.private, "locked_by": None, "lease_expires_at": None},
                    ),
                )
                await transaction.audit(
                    None,
                    record.private["correlation_id"],
                    "publication." + ("retry" if retry else "failed"),
                    lease.job_id,
                    outcome="FAILURE",
                    error_code=code,
                    initiator_id=uuid(record.private["requested_by"]),
                )
        except LeaseLost:
            pass

    async def execute(self, lease):
        async with self.guard(lease) as (transaction, record, _):
            identity = await transaction.identity_for_principal(
                uuid(record.private["requested_by"])
            )
            job = record.public
        sources = WorkerSources(self, lease)
        products = WorkerProducts(self, lease)
        grants = WorkerGrants(self, lease)
        contracts = WorkerContracts(self, lease)
        authorization = WorkerIdentity(self, lease)
        if job["job_type"] == "CONNECT_SOURCE":
            source = await sources.get(uuid(job["data_source_id"]))
            async with self.guard(lease) as (transaction, _, _):
                await source_affiliation(transaction, await transaction.get("source", source.id))
            await self.service.discovery.policy.check(source, source.name)
            await SourceRegistrationService(
                self.client,
                sources,
                self.service.discovery.policy.secrets,
                self.settings,
                authorization,
            ).register(source.id, identity, expected_revision=job["target_revision"])
        elif job["job_type"] == "PUBLISH_PRODUCT":
            async with self.guard(lease) as (transaction, _, _):
                product = await transaction.get("product", uuid(job["data_product_id"]))
                contract = await transaction.get(
                    "contract", uuid(product.public["active_contract_id"])
                )
            mapping = {
                field["column"]: field["name"]
                for field in contract.public["contract_json"]["fields"]
            }
            metadata = PublicationMetadata(self.client, mapping)
            await DataProductPublisher(
                metadata, sources, products, contracts, authorization
            ).publish(
                uuid(job["data_product_id"]), identity, expected_revision=job["target_revision"]
            )
        else:
            service = HasuraPermissionService(
                self.client, sources, products, grants, contracts, self.settings, authorization
            )
            if job["job_type"] == "DISABLE_PRODUCT":
                async with self.guard(lease) as (transaction, _, _):
                    affected = await transaction.find(
                        "grant", data_product_id=job["data_product_id"]
                    )
                for department_id in sorted(
                    {record.public["consumer_department_id"] for record in affected}
                ):
                    await service.reconcile(
                        PermissionTarget(
                            product_id=job["data_product_id"], department_id=department_id
                        ),
                        identity,
                    )
            else:
                grant = await grants.get(uuid(job["access_grant_id"]))
                target = PermissionTarget(
                    product_id=grant.product_id, department_id=grant.department_id
                )
                if job["job_type"] == "REVOKE_GRANT":
                    await service.revoke(
                        target, grant.id, identity, expected_revision=job["target_revision"]
                    )
                else:
                    await service.reconcile(
                        target,
                        identity,
                        expected_grant_id=grant.id,
                        expected_revision=job["target_revision"],
                    )


class WorkerIdentity:
    def __init__(self, worker, lease):
        self.worker, self.lease = worker, lease

    async def require_admin(self, actor, action):
        async with self.worker.guard(self.lease) as (transaction, record, _):
            principal = await resolve_principal(transaction, actor)
            principal.require_admin()
            if str(principal.principal_id) != record.private["requested_by"]:
                raise CoreError(ErrorCode.FORBIDDEN)


class PublicationMetadata:
    def __init__(self, client, mapping):
        self.client, self.mapping = client, mapping

    async def track_table(self, source, table):
        await self.client.track_table(source, table)
        await self.client.configure_read_mapping(source, table, self.mapping)

    async def require_consistent(self):
        await self.client.require_consistent()


class WorkerSources(WorkerIdentity):
    async def get(self, source_id):
        async with self.worker.guard(self.lease) as (transaction, _, _):
            return source_record(await transaction.get("source", source_id))

    async def mark_connected(self, source_id, revision):
        async with self.worker.guard(self.lease) as (transaction, _, now):
            source = await transaction.get("source", source_id)
            if source.public["revision"] != revision or source.public["status"] == "DISABLED":
                raise MetadataConflict()
            await source_affiliation(transaction, source)
            await transaction.replace(
                "source",
                StoredResource(
                    {
                        **source.public,
                        "status": "CONNECTED",
                        "updated_by": None,
                        "last_checked_at": timestamp(now),
                        "updated_at": timestamp(now),
                    },
                    source.private,
                ),
            )


class WorkerProducts(WorkerIdentity):
    async def get(self, product_id):
        async with self.worker.guard(self.lease) as (transaction, _, _):
            product = (await transaction.get("product", product_id)).public
            return ProductRecord(
                id=product["id"],
                source_id=product["data_source_id"],
                table=TableRef(schema=product["schema_name"], name=product["object_name"]),
                contract_id=product["active_contract_id"],
                status=product["status"],
                approved=product["status"] != "DISABLED",
                revision=product["revision"],
            )

    async def mark_active(self, product_id, revision):
        async with self.worker.guard(self.lease) as (transaction, _, now):
            product = await transaction.get("product", product_id)
            if product.public["revision"] != revision or product.public["status"] not in {
                "PROVISIONING",
                "ACTIVE",
            }:
                raise MetadataConflict()
            contract = await transaction.get("contract", uuid(product.public["active_contract_id"]))
            await self.worker.service.verify_contract(transaction, product, contract)
            await transaction.replace(
                "product",
                StoredResource(
                    {
                        **product.public,
                        "status": "ACTIVE",
                        "updated_by": None,
                        "updated_at": timestamp(now),
                    },
                    product.private,
                ),
            )


class WorkerContracts(WorkerIdentity):
    async def validate(self, source, product, *, contract_id=None):
        async with self.worker.guard(self.lease) as (transaction, _, _):
            contract = await transaction.get("contract", contract_id or product.contract_id)
            stored = await transaction.get("product", product.id)
            if (
                contract.public["status"] != "PUBLISHED"
                or contract.public["id"] != stored.public["active_contract_id"]
                or contract.public["data_product_id"] != str(product.id)
            ):
                raise CoreError(ErrorCode.INVALID_STATE)
            await self.worker.service.verify_contract(transaction, stored, contract)
            return ValidatedContract(
                product_id=product.id,
                contract_id=contract.public["id"],
                status="PUBLISHED",
                columns=tuple(
                    field["column"] for field in contract.public["contract_json"]["fields"]
                ),
            )


class WorkerGrants(WorkerIdentity):
    @staticmethod
    async def projection(transaction, resource):
        data = resource.public
        department = await transaction.get("department", uuid(data["consumer_department_id"]))
        request = await transaction.get("request", uuid(data["access_request_id"]))
        return SelectGrant(
            **{
                key: data[key]
                for key in [
                    "id",
                    "data_product_id",
                    "contract_version_id",
                    "consumer_department_id",
                    "consumer_principal_id",
                    "scope_json",
                    "status",
                    "expires_at",
                    "desired_revision",
                    "applied_revision",
                ]
            },
            approved=(
                department.public["status"] == "ACTIVE" and request.public["status"] == "APPROVED"
            ),
        )

    async def get(self, grant_id):
        async with self.worker.guard(self.lease) as (transaction, _, _):
            return await self.projection(transaction, await transaction.get("grant", grant_id))

    def lock(self, target):
        return self.worker.store.permission_lock(target.product_id, target.department_id)

    async def list_for_target(self, target):
        async with self.worker.guard(self.lease) as (transaction, _, _):
            grants = await transaction.find(
                "grant",
                data_product_id=str(target.product_id),
                consumer_department_id=str(target.department_id),
            )
            return [await self.projection(transaction, grant) for grant in grants]

    async def transition(self, grant_id, revision, status):
        async with self.worker.guard(self.lease) as (transaction, _, now):
            grant = await transaction.get("grant", grant_id)
            if grant.public["desired_revision"] != revision:
                raise MetadataConflict()
            if status == "ACTIVE":
                from app.core.governance.service import active_department, current_product

                await current_product(
                    transaction,
                    grant.public["data_product_id"],
                    grant.public["contract_version_id"],
                )
                await active_department(transaction, uuid(grant.public["consumer_department_id"]))
                if (
                    grant.public["status"] not in {"PENDING_APPLY", "ACTIVE", "ERROR"}
                    or grant.public["expires_at"]
                    and instant(grant.public["expires_at"]) <= now
                ):
                    raise MetadataConflict()
            await transaction.replace(
                "grant",
                StoredResource(
                    {
                        **grant.public,
                        "status": status,
                        "applied_revision": revision,
                        "updated_at": timestamp(now),
                    },
                    grant.private,
                ),
            )

    async def mark_applied(self, grant_id, revision):
        await self.transition(grant_id, revision, "ACTIVE")

    async def mark_revoked(self, grant_id, revision):
        await self.transition(grant_id, revision, "REVOKED")

    async def mark_failed(self, grant_id, revision, error_code):
        async with self.worker.guard(self.lease) as (transaction, _, now):
            grant = await transaction.get("grant", grant_id)
            if grant.public["desired_revision"] != revision:
                raise MetadataConflict()
            await transaction.replace(
                "grant",
                StoredResource(
                    {**grant.public, "status": "ERROR", "updated_at": timestamp(now)}, grant.private
                ),
            )
