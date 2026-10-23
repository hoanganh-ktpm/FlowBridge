import asyncio
import json
import re
from collections.abc import Mapping

from app.core.applications.manifest import fingerprint
from app.core.db.ports import StoredResource
from app.core.hasura_provisioner.errors import InvalidConfiguration
from app.core.hasura_provisioner.ports import SecretResolver
from app.core.hasura_provisioner.schemas import SourceRecord, TableRef
from app.platform.http.errors import CoreError, ErrorCode

from .discovery import DiscoverySource, SchemaDiscovery, SourceSnapshot


def source_record(resource: StoredResource) -> SourceRecord:
    return SourceRecord(
        id=resource.public["id"],
        name=resource.public["hasura_source_name"],
        status=resource.public["status"],
        revision=resource.public["revision"],
        approved=resource.public["status"] != "DISABLED",
        **resource.private,
    )


class ApprovedSourcePolicy:
    def __init__(
        self,
        secrets: SecretResolver,
        allowed_hosts: frozenset[str],
        scopes: Mapping[str, tuple[TableRef, ...]],
    ) -> None:
        self.secrets = secrets
        self.allowed_hosts = allowed_hosts
        self.scopes = dict(scopes)

    async def check(self, source: SourceRecord, key: str) -> DiscoverySource:
        connection = await self.secrets.resolve_postgres(source.secret_ref)
        scope = self.scopes.get(source.secret_ref, ())
        if (
            not scope
            or len(scope) > 1000
            or len(set((item.schema_name, item.name) for item in scope)) != len(scope)
            or connection.hostname not in self.allowed_hosts
            or not source.matches_connection(connection)
        ):
            raise InvalidConfiguration()
        return DiscoverySource(
            key=key,
            host=source.host,
            port=source.port,
            database=source.database_name,
            username=source.db_user,
            ssl_mode=source.ssl_mode,
            secret_ref=source.secret_ref,
            objects=scope,
        )


class SourceDiscoveryService:
    def __init__(self, discovery: SchemaDiscovery, policy: ApprovedSourcePolicy) -> None:
        self.discovery = discovery
        self.policy = policy

    async def snapshot(
        self, resource: StoredResource, schema: str | None = None, names: tuple[str, ...] = ()
    ) -> SourceSnapshot:
        source = await self.policy.check(source_record(resource), resource.public["source_key"])
        scope = tuple(
            table
            for table in source.objects
            if (schema is None or table.schema_name == schema)
            and (not names or table.name in names)
        )
        if not scope or (names and set(names) - {table.name for table in scope}):
            raise CoreError(ErrorCode.FORBIDDEN)
        try:
            async with asyncio.timeout(20):
                snapshot = await self.discovery.discover(
                    source.model_copy(update={"objects": scope})
                )
        except TimeoutError:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        for item in snapshot.objects:
            identifiers = [
                item.table.schema_name,
                item.table.name,
                *(column.name for column in item.columns),
                *item.primary_key,
            ]
            keys = [item.primary_key, *item.unique_constraints]
            for key in item.unique_constraints:
                identifiers.extend(key)
            for key in item.foreign_keys:
                identifiers.extend(
                    [*key.columns, key.target.schema_name, key.target.name, *key.target_columns]
                )
                keys.extend([key.columns, key.target_columns])
            if (
                any(not re.fullmatch(r"(?!__)[a-z_][a-z0-9_]{0,62}", name) for name in identifiers)
                or any(len(key) > 20 for key in keys)
                or any(not 1 <= len(column.pg_type) <= 100 for column in item.columns)
                or any(
                    key.name is not None and not 1 <= len(key.name) <= 63
                    for key in item.foreign_keys
                )
            ):
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        if (
            snapshot.source_key != source.key
            or len(snapshot.objects) > 1000
            or any(
                item.table not in scope
                or not 1 <= len(item.columns) <= 1000
                or len(item.unique_constraints) > 100
                or len(item.foreign_keys) > 100
                for item in snapshot.objects
            )
            or len(snapshot.model_dump_json().encode()) > 8 * 1024 * 1024
        ):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return snapshot

    @staticmethod
    def projection(source_id: str, snapshot: SourceSnapshot, captured_at: str) -> dict:
        objects = [
            {
                "schema_name": item.table.schema_name,
                "object_name": item.table.name,
                "object_type": item.kind.upper(),
                "columns": [
                    {"name": field.name, "physical_type": field.pg_type, "nullable": field.nullable}
                    for field in item.columns
                ],
                "primary_key": list(item.primary_key),
                "unique_constraints": [list(key) for key in item.unique_constraints],
                "foreign_keys": [
                    {
                        "name": key.name,
                        "columns": list(key.columns),
                        "target_schema": key.target.schema_name,
                        "target_object": key.target.name,
                        "target_columns": list(key.target_columns),
                    }
                    for key in item.foreign_keys
                ],
            }
            for item in snapshot.objects
        ]
        json.dumps(objects, allow_nan=False)
        return {
            "data_source_id": source_id,
            "captured_at": captured_at,
            "schema_hash": fingerprint(objects),
            "objects": objects,
        }
