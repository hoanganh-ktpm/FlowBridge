import re
import ssl
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Inspector, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.hasura_provisioner.errors import HasuraError, InvalidConfiguration
from app.core.hasura_provisioner.ports import SecretResolver
from app.core.hasura_provisioner.schemas import DTO, SourceRecord, TableRef


class DiscoveryError(ValueError):
    def __init__(self) -> None:
        super().__init__(
            "Schema discovery unavailable; check approved connection and reader privileges"
        )


class Column(DTO):
    name: str
    pg_type: str
    nullable: bool


class ForeignKey(DTO):
    name: str | None = None
    columns: tuple[str, ...] = Field(min_length=1)
    target: TableRef
    target_columns: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def equal_arity(self) -> "ForeignKey":
        if len(self.columns) != len(self.target_columns):
            raise ValueError("Foreign key arity mismatch")
        return self


class DiscoveredObject(DTO):
    table: TableRef
    kind: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    unique_constraints: tuple[tuple[str, ...], ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()

    @model_validator(mode="after")
    def validate_keys(self) -> "DiscoveredObject":
        names = [column.name for column in self.columns]
        if len(set(names)) != len(names) or self.kind not in {"table", "view"}:
            raise ValueError("Invalid discovered object")
        for key in (self.primary_key, *self.unique_constraints):
            if not set(key) <= set(names):
                raise ValueError("Key references an unknown column")
        return self


class SourceSnapshot(DTO):
    source_key: str
    schemas: tuple[str, ...]
    objects: tuple[DiscoveredObject, ...]

    @model_validator(mode="after")
    def unique_objects(self) -> "SourceSnapshot":
        names = [(item.table.schema_name, item.table.name) for item in self.objects]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate physical objects")
        return self

    def find(self, table: TableRef) -> DiscoveredObject | None:
        return next((item for item in self.objects if item.table == table), None)


class DiscoverySource(DTO):
    key: str
    host: str
    port: int
    database: str
    username: str
    ssl_mode: str
    secret_ref: str
    objects: tuple[TableRef, ...]


class SchemaDiscovery(Protocol):
    async def discover(self, source: DiscoverySource) -> SourceSnapshot: ...


def inspect_schema(
    connection: Connection, source: DiscoverySource, inspector_factory: Callable = inspect
) -> SourceSnapshot:
    inspector: Inspector = inspector_factory(connection)
    schemas = sorted(inspector.get_schema_names())
    objects = []
    for schema in sorted({table.schema_name for table in source.objects}):
        if schema not in schemas:
            continue
        tables = set(inspector.get_table_names(schema=schema))
        views = set(inspector.get_view_names(schema=schema))
        for table in sorted(source.objects, key=lambda item: (item.schema_name, item.name)):
            if table.schema_name != schema or table.name not in tables | views:
                continue
            columns = tuple(
                Column(name=item["name"], pg_type=str(item["type"]), nullable=item["nullable"])
                for item in inspector.get_columns(table.name, schema=schema)
            )
            primary = inspector.get_pk_constraint(table.name, schema=schema)
            unique = inspector.get_unique_constraints(table.name, schema=schema)
            foreign = inspector.get_foreign_keys(
                table.name, schema=schema, postgresql_ignore_search_path=True
            )
            objects.append(
                DiscoveredObject(
                    table=table,
                    kind="view" if table.name in views else "table",
                    columns=columns,
                    primary_key=tuple(primary.get("constrained_columns") or ()),
                    unique_constraints=tuple(
                        tuple(item["column_names"]) for item in unique if item.get("column_names")
                    ),
                    foreign_keys=tuple(
                        ForeignKey(
                            name=item.get("name"),
                            columns=tuple(item["constrained_columns"]),
                            target=TableRef(
                                schema=item.get("referred_schema") or schema,
                                name=item["referred_table"],
                            ),
                            target_columns=tuple(item["referred_columns"]),
                        )
                        for item in foreign
                    ),
                )
            )
    return SourceSnapshot(source_key=source.key, schemas=tuple(schemas), objects=tuple(objects))


class PostgresSchemaDiscovery:
    def __init__(self, secrets: SecretResolver, allowed_hosts: frozenset[str]) -> None:
        self._secrets = secrets
        self._hosts = allowed_hosts

    async def discover(self, source: DiscoverySource) -> SourceSnapshot:
        connection = await self._secrets.resolve_postgres(source.secret_ref)
        record = SourceRecord(
            id=UUID(int=0),
            name="discovery",
            host=source.host,
            port=source.port,
            database_name=source.database,
            db_user=source.username,
            ssl_mode=source.ssl_mode,
            secret_ref=source.secret_ref,
            revision=1,
        )
        if connection.hostname not in self._hosts or not record.matches_connection(connection):
            raise InvalidConfiguration()
        uri = make_url(connection.database_url.get_secret_value())
        tls = ssl.create_default_context()
        if source.ssl_mode == "require":
            tls.check_hostname = False
            tls.verify_mode = ssl.CERT_NONE
        elif source.ssl_mode == "verify-ca":
            tls.check_hostname = False
        arguments = {
            "ssl": tls,
            "timeout": float(uri.query.get("connect_timeout", 10)),
            "server_settings": {"application_name": "flowbridge_schema_discovery"},
        }
        engine = create_async_engine(
            uri.set(drivername="postgresql+asyncpg", query={}),
            poolclass=NullPool,
            connect_args=arguments,
            echo=False,
            hide_parameters=True,
        )
        try:
            async with engine.connect() as database:
                await database.execute(text("SET TRANSACTION READ ONLY"))
                await database.execute(text("SET LOCAL statement_timeout = '5s'"))
                return await database.run_sync(lambda sync: inspect_schema(sync, source))
        except HasuraError:
            raise
        except Exception:
            raise DiscoveryError() from None
        finally:
            await engine.dispose()


def canonical_type(value: str) -> str:
    value = " ".join(value.lower().split())
    aliases = {
        "varchar": "text",
        "character varying": "text",
        "int4": "integer",
        "int8": "bigint",
        "int2": "smallint",
        "decimal": "numeric",
        "bool": "boolean",
        "timestamp with time zone": "timestamptz",
        "timestamp without time zone": "timestamp",
    }
    head = " ".join(re.sub(r"\([^)]*\)", "", value).split())
    return aliases.get(head, head)


def fk_suggestions(snapshot: SourceSnapshot) -> list[dict]:
    return [
        {
            "source": item.table.payload(),
            "target": foreign.target.payload(),
            "mapping": dict(zip(foreign.columns, foreign.target_columns, strict=True)),
            "constraint": foreign.name,
            "confirmed": False,
        }
        for item in snapshot.objects
        for foreign in item.foreign_keys
    ]
