import logging
from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError

from .errors import (
    HasuraRejected,
    HasuraTimeout,
    HasuraUnavailable,
    InvalidConfiguration,
    InvalidMetadata,
    MetadataConflict,
)
from .planning import postgres_source_args
from .schemas import (
    ConsistencyReport,
    MetadataSnapshot,
    PostgresConnection,
    SelectPermission,
    SourceSummary,
    TableRef,
    department_role,
)
from .settings import HasuraSettings

logger = logging.getLogger(__name__)
PERMISSION_OWNER = "flowbridge:select:v1"


def sources_in(snapshot: MetadataSnapshot) -> list[dict[str, Any]]:
    sources = snapshot.metadata.get("sources")
    if not isinstance(sources, list) or any(
        not isinstance(source, dict)
        or not isinstance(source.get("name"), str)
        or not isinstance(source.get("kind"), str)
        for source in sources
    ):
        raise InvalidMetadata()
    names = [source["name"] for source in sources]
    if len(names) != len(set(names)):
        raise InvalidMetadata()
    return sources


def source_in(snapshot: MetadataSnapshot, name: str) -> dict[str, Any] | None:
    return next((source for source in sources_in(snapshot) if source["name"] == name), None)


def table_in(source: dict[str, Any], table: TableRef) -> dict[str, Any] | None:
    tables = source.get("tables", [])
    if not isinstance(tables, list) or any(not isinstance(item, dict) for item in tables):
        raise InvalidMetadata()
    return next((item for item in tables if item.get("table") == table.payload()), None)


def contains_configuration(actual: Any, desired: Any) -> bool:
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(
            key in actual and contains_configuration(actual[key], value)
            for key, value in desired.items()
        )
    return type(actual) is type(desired) and actual == desired


def matches_source(
    source: dict[str, Any] | None,
    configuration: dict[str, Any],
    customization: dict[str, Any],
) -> bool:
    if source is None or source.get("kind") != "postgres":
        return False
    actual = source.get("configuration")
    if not isinstance(actual, dict) or any(
        actual.get(key) for key in ("read_replicas", "connection_template", "connection_set")
    ):
        return False
    return contains_configuration(actual, configuration) and contains_configuration(
        source.get("customization"), customization
    )


def matches_permission(actual: Any, desired: dict[str, Any]) -> bool:
    if not isinstance(actual, dict):
        return False
    normalized = dict(actual)
    if normalized.pop("computed_fields", []) or normalized.pop("backend_only", False):
        return False
    columns = normalized.get("columns")
    if not isinstance(columns, list) or any(not isinstance(column, str) for column in columns):
        return False
    normalized["columns"] = sorted(set(columns))
    normalized.setdefault("allow_aggregations", False)
    return normalized == desired


class HasuraMetadataClient:
    def __init__(self, settings: HasuraSettings, http: httpx.AsyncClient) -> None:
        if not settings.admin_secret.get_secret_value():
            raise InvalidConfiguration()
        self._settings = settings
        self._http = http

    async def _request(self, operation: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._http.request(
                "GET" if payload is None else "POST",
                self._settings.endpoint + ("/healthz" if payload is None else "/v1/metadata"),
                headers={"x-hasura-admin-secret": self._settings.admin_secret.get_secret_value()},
                json=payload,
                timeout=self._settings.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            logger.warning("Hasura operation=%s failure=timeout", operation)
            raise HasuraTimeout() from None
        except httpx.HTTPError:
            logger.warning("Hasura operation=%s failure=transport", operation)
            raise HasuraUnavailable() from None
        if response.status_code >= 500:
            raise HasuraUnavailable()
        if response.status_code == 409:
            raise MetadataConflict()
        if not 200 <= response.status_code < 300:
            try:
                error = response.json()
            except ValueError:
                error = None
            if isinstance(error, dict) and error.get("code") in ("conflict", "already-exists"):
                raise MetadataConflict()
            raise HasuraRejected()
        if payload is None:
            if response.text.strip() != "OK":
                raise InvalidMetadata()
            return True
        try:
            result = response.json()
        except ValueError:
            raise InvalidMetadata() from None
        if not isinstance(result, dict):
            raise InvalidMetadata()
        if "error" in result or "code" in result:
            if result.get("code") in ("conflict", "already-exists"):
                raise MetadataConflict()
            raise HasuraRejected()
        return result

    async def _metadata(
        self,
        operation: str,
        args: dict[str, Any],
        *,
        version: int = 1,
        resource_version: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": operation, "version": version, "args": args}
        if resource_version is not None:
            payload["resource_version"] = resource_version
        return await self._request(operation, payload)

    async def _write(
        self, operation: str, args: dict[str, Any], snapshot: MetadataSnapshot
    ) -> None:
        result = await self._metadata(operation, args, resource_version=snapshot.resource_version)
        if result.get("message") != "success":
            raise InvalidMetadata()

    async def check_health(self) -> bool:
        return await self._request("health")

    async def export_metadata(self) -> MetadataSnapshot:
        result = await self._metadata("export_metadata", {}, version=2)
        try:
            snapshot = MetadataSnapshot.model_validate(result)
        except ValidationError:
            raise InvalidMetadata() from None
        if snapshot.metadata.get("version") != 3:
            raise InvalidMetadata()
        sources_in(snapshot)
        return snapshot

    async def list_sources(self) -> list[SourceSummary]:
        return [
            SourceSummary(name=source["name"], kind=source["kind"])
            for source in sources_in(await self.export_metadata())
        ]

    async def add_postgres_source(self, name: str, connection: PostgresConnection) -> None:
        arguments = postgres_source_args(name, connection.environment_name)
        configuration = arguments["configuration"]
        customization = arguments["customization"]
        snapshot = await self.export_metadata()
        existing = source_in(snapshot, name)
        if existing is not None:
            if not matches_source(existing, configuration, customization):
                raise MetadataConflict()
            return
        await self._write(
            "pg_add_source",
            arguments,
            snapshot,
        )
        verified = source_in(await self.export_metadata(), name)
        if not matches_source(verified, configuration, customization):
            raise InvalidMetadata()

    async def track_table(self, source: str, table: TableRef) -> None:
        snapshot = await self.export_metadata()
        existing = source_in(snapshot, source)
        if existing is None or existing.get("kind") != "postgres":
            raise InvalidConfiguration()
        if table_in(existing, table) is not None:
            return
        await self._write("pg_track_table", {"source": source, "table": table.payload()}, snapshot)
        verified = source_in(await self.export_metadata(), source)
        if verified is None or table_in(verified, table) is None:
            raise InvalidMetadata()

    async def _permission_state(
        self, source: str, table: TableRef, department_id: UUID, *, required: bool = True
    ) -> tuple[MetadataSnapshot, dict[str, Any] | None]:
        snapshot = await self.export_metadata()
        registered = source_in(snapshot, source)
        if registered is None:
            if not required:
                return snapshot, None
            raise InvalidConfiguration()
        if registered.get("kind") != "postgres":
            raise InvalidConfiguration()
        tracked = table_in(registered, table)
        if tracked is None:
            if not required:
                return snapshot, None
            raise InvalidConfiguration()
        permissions = tracked.get("select_permissions", [])
        if not isinstance(permissions, list) or any(
            not isinstance(permission, dict) for permission in permissions
        ):
            raise InvalidMetadata()
        role = department_role(department_id)
        matches = [permission for permission in permissions if permission.get("role") == role]
        if len(matches) > 1:
            raise InvalidMetadata()
        existing = matches[0] if matches else None
        if existing is not None and existing.get("comment") != PERMISSION_OWNER:
            raise MetadataConflict()
        return snapshot, existing

    async def configure_read_mapping(
        self, source: str, table: TableRef, aliases: dict[str, str]
    ) -> None:
        snapshot = await self.export_metadata()
        registered = source_in(snapshot, source)
        tracked = table_in(registered, table) if registered else None
        if (
            registered is None
            or tracked is None
            or registered.get("customization", {}).get("root_fields") != {"namespace": source}
        ):
            raise InvalidConfiguration()
        configuration = tracked.get("configuration", {})
        if any(value for key, value in configuration.items() if key != "column_config"):
            raise MetadataConflict()
        desired = {
            "column_config": {
                column: {"custom_name": name} for column, name in aliases.items() if column != name
            }
        }
        if configuration.get("column_config", {}) != desired["column_config"]:
            if configuration.get("column_config"):
                raise MetadataConflict()
            await self._write(
                "pg_set_table_customization",
                {"source": source, "table": table.payload(), "configuration": desired},
                snapshot,
            )
        verified = source_in(await self.export_metadata(), source)
        tracked = table_in(verified, table) if verified else None
        if (
            tracked is None
            or tracked.get("configuration", {}).get("column_config", {}) != desired["column_config"]
        ):
            raise InvalidMetadata()

    async def create_select_permission(
        self, source: str, table: TableRef, department_id: UUID, permission: SelectPermission
    ) -> None:
        snapshot, existing = await self._permission_state(source, table, department_id)
        desired = permission.payload()
        if existing is not None and matches_permission(existing.get("permission"), desired):
            return
        if existing is not None:
            await self._write(
                "pg_drop_select_permission",
                {
                    "source": source,
                    "table": table.payload(),
                    "role": department_role(department_id),
                },
                snapshot,
            )
            snapshot, existing = await self._permission_state(source, table, department_id)
            if existing is not None:
                raise MetadataConflict()
        await self._write(
            "pg_create_select_permission",
            {
                "source": source,
                "table": table.payload(),
                "role": department_role(department_id),
                "permission": desired,
                "comment": PERMISSION_OWNER,
            },
            snapshot,
        )
        _, verified = await self._permission_state(source, table, department_id)
        if verified is None or not matches_permission(verified.get("permission"), desired):
            raise InvalidMetadata()

    async def drop_select_permission(
        self, source: str, table: TableRef, department_id: UUID
    ) -> None:
        snapshot, existing = await self._permission_state(
            source, table, department_id, required=False
        )
        if existing is None:
            return
        await self._write(
            "pg_drop_select_permission",
            {"source": source, "table": table.payload(), "role": department_role(department_id)},
            snapshot,
        )
        _, verified = await self._permission_state(source, table, department_id, required=False)
        if verified is not None:
            raise InvalidMetadata()

    async def reload_metadata(self, sources: list[str]) -> ConsistencyReport:
        if not sources:
            raise InvalidConfiguration()
        result = await self._metadata(
            "reload_metadata",
            {
                "reload_sources": sources,
                "reload_remote_schemas": False,
                "recreate_event_triggers": False,
            },
        )
        if result.get("message") != "success":
            raise InvalidMetadata()
        if result.get("is_consistent") is True and "inconsistent_objects" not in result:
            result = {**result, "inconsistent_objects": []}
        return self._consistency(result)

    async def get_inconsistent_metadata(self) -> ConsistencyReport:
        return self._consistency(await self._metadata("get_inconsistent_metadata", {}))

    @staticmethod
    def _consistency(result: dict[str, Any]) -> ConsistencyReport:
        objects = result.get("inconsistent_objects")
        if not isinstance(result.get("is_consistent"), bool) or not isinstance(objects, list):
            raise InvalidMetadata()
        if result["is_consistent"] != (len(objects) == 0):
            raise InvalidMetadata()
        return ConsistencyReport(
            is_consistent=result["is_consistent"], inconsistent_count=len(objects)
        )

    async def require_consistent(self) -> None:
        if not (await self.get_inconsistent_metadata()).is_consistent:
            raise InvalidMetadata()
