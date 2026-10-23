import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

import httpx

from app.core.hasura_provisioner.errors import (
    ContractRejected,
    MetadataConflict,
    ProvisioningDenied,
)
from app.core.hasura_provisioner.schemas import (
    PermissionTarget,
    ProductRecord,
    SelectGrant,
    SourceRecord,
    ValidatedContract,
)

SOURCE_ID = UUID(int=1)
PRODUCT_ID = UUID(int=2)
CONTRACT_ID = UUID(int=3)
DEPARTMENT_ID = UUID(int=4)


class Engine:
    def __init__(self) -> None:
        self.version = 1
        self.metadata = {"version": 3, "sources": []}
        self.requests = []
        self.failures = {}
        self.consistent = True
        self.ack_without_apply = False
        self.timeout_after_apply = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["x-hasura-admin-secret"] == "only-a-test-admin-secret"
        if request.url.path == "/healthz":
            assert request.method == "GET"
            return httpx.Response(200, text="OK")
        assert request.url.path == "/v1/metadata"
        assert request.method == "POST"
        payload = json.loads(request.content)
        self.requests.append(deepcopy(payload))
        operation = payload["type"]
        args = payload["args"]
        assert payload["version"] == (2 if operation == "export_metadata" else 1)
        failure = self.failures.get(operation)
        if isinstance(failure, Exception):
            raise failure
        if isinstance(failure, httpx.Response):
            return failure
        if operation == "export_metadata":
            assert payload["version"] == 2
            return httpx.Response(
                200, json={"resource_version": self.version, "metadata": deepcopy(self.metadata)}
            )
        if operation in {"get_inconsistent_metadata", "reload_metadata"}:
            if operation == "reload_metadata" and self.consistent:
                return httpx.Response(200, json={"is_consistent": True, "message": "success"})
            return httpx.Response(
                200,
                json={
                    **({"message": "success"} if operation == "reload_metadata" else {}),
                    "is_consistent": self.consistent,
                    "inconsistent_objects": [] if self.consistent else [{"reason": "unavailable"}],
                },
            )
        if payload.get("resource_version") != self.version:
            return httpx.Response(409, json={"code": "conflict", "error": "resource version"})
        if self.ack_without_apply:
            return httpx.Response(200, json={"message": "success"})
        if operation == "pg_add_source":
            if any(source["name"] == args["name"] for source in self.metadata["sources"]):
                return httpx.Response(400, json={"code": "already-exists", "error": "duplicate"})
            self.metadata["sources"].append(
                {
                    "name": args["name"],
                    "kind": "postgres",
                    "tables": [],
                    "configuration": args["configuration"],
                    "customization": args["customization"],
                }
            )
        elif operation == "pg_track_table":
            source = self.source(args["source"])
            if any(item["table"] == args["table"] for item in source["tables"]):
                return httpx.Response(400, json={"code": "already-exists", "error": "tracked"})
            source["tables"].append({"table": args["table"]})
        elif operation == "pg_create_select_permission":
            table = self.table(args["source"], args["table"])
            if any(item["role"] == args["role"] for item in table.get("select_permissions", [])):
                return httpx.Response(400, json={"code": "already-exists", "error": "permission"})
            assert set(args) == {"source", "table", "role", "permission", "comment"}
            assert args["permission"]["filter"] == {}
            assert args["permission"]["columns"] != "*"
            assert args["permission"]["query_root_fields"] == ["select"]
            assert args["permission"]["subscription_root_fields"] == []
            assert args["permission"]["allow_aggregations"] is False
            table.setdefault("select_permissions", []).append(
                {
                    "role": args["role"],
                    "permission": args["permission"],
                    "comment": args["comment"],
                }
            )
        elif operation == "pg_drop_select_permission":
            table = self.table(args["source"], args["table"])
            table["select_permissions"] = [
                permission
                for permission in table.get("select_permissions", [])
                if permission["role"] != args["role"]
            ]
        else:
            raise AssertionError(f"Unexpected operation: {operation}")
        self.version += 1
        if operation in self.timeout_after_apply:
            self.timeout_after_apply.remove(operation)
            raise httpx.ReadTimeout("mock response lost after mutation")
        return httpx.Response(200, json={"message": "success"})

    def source(self, name="warehouse"):
        return next(source for source in self.metadata["sources"] if source["name"] == name)

    def table(self, source="warehouse", table=None):
        table = table or {"schema": "public", "name": "stock_available"}
        return next(item for item in self.source(source)["tables"] if item["table"] == table)

    def writes(self):
        return [request for request in self.requests if request["type"].startswith("pg_")]


class Sources:
    def __init__(self, record: SourceRecord) -> None:
        self.record = record
        self.connected = []

    async def get(self, source_id):
        assert source_id == self.record.id
        return self.record

    async def mark_connected(self, source_id, revision):
        if revision != self.record.revision:
            raise MetadataConflict()
        self.connected.append(source_id)
        self.record = self.record.model_copy(update={"status": "CONNECTED"})


class Products:
    def __init__(self, record: ProductRecord) -> None:
        self.record = record
        self.active = []

    async def get(self, product_id):
        assert product_id == self.record.id
        return self.record

    async def mark_active(self, product_id, revision):
        if revision != self.record.revision:
            raise MetadataConflict()
        self.active.append(product_id)
        self.record = self.record.model_copy(update={"status": "ACTIVE"})


class Grants:
    def __init__(self) -> None:
        self.records: list[SelectGrant] = []
        self.applied = []
        self.revoked = []
        self.failed = []
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def lock(self, target: PermissionTarget):
        async with self._lock:
            yield

    async def list_for_target(self, target):
        return list(self.records)

    async def get(self, grant_id):
        return next(grant for grant in self.records if grant.id == grant_id)

    async def mark_applied(self, grant_id, revision):
        current = await self.get(grant_id)
        if (
            current.desired_revision != revision
            or not current.approved
            or current.status not in {"PENDING_APPLY", "ACTIVE", "ERROR"}
            or (current.expires_at is not None and current.expires_at <= datetime.now(UTC))
        ):
            raise MetadataConflict()
        self.records[self.records.index(current)] = current.model_copy(
            update={
                "status": "ACTIVE",
                "applied_revision": revision,
            }
        )
        self.applied.append((grant_id, revision))

    async def mark_revoked(self, grant_id, revision):
        current = await self.get(grant_id)
        if current.desired_revision != revision:
            raise MetadataConflict()
        expired = current.expires_at is not None and current.expires_at <= datetime.now(UTC)
        if current.status not in {"REVOKING", "REVOKED", "EXPIRED"} and not expired:
            raise MetadataConflict()
        self.records[self.records.index(current)] = current.model_copy(
            update={
                "status": "EXPIRED" if expired or current.status == "EXPIRED" else "REVOKED",
                "applied_revision": revision,
            }
        )
        self.revoked.append((grant_id, revision))

    async def mark_failed(self, grant_id, revision, error_code):
        current = await self.get(grant_id)
        if current.desired_revision != revision:
            raise MetadataConflict()
        self.records[self.records.index(current)] = current.model_copy(update={"status": "ERROR"})
        self.failed.append((grant_id, revision, error_code))


class Contracts:
    def __init__(self) -> None:
        self.reject = False
        self.rejected_contracts = set()
        self.status = "PUBLISHED"
        self.validated_ids = []

    async def validate(self, source, product, *, contract_id=None):
        contract_id = contract_id or product.contract_id
        self.validated_ids.append(contract_id)
        if self.reject or contract_id in self.rejected_contracts:
            raise ContractRejected()
        return ValidatedContract(
            product_id=PRODUCT_ID,
            contract_id=contract_id,
            status=self.status,
            columns=("sku", "available_quantity"),
        )


class Admin:
    async def require_admin(self, actor, action):
        if actor.subject != "test-admin":
            raise ProvisioningDenied()
