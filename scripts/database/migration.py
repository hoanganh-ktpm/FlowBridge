"""Programmatic Alembic setup; migrations contain frozen DDL, not create_all()."""

from copy import copy

from alembic.autogenerate.rewriter import Rewriter
from alembic.config import Config
from alembic.operations import Operations, ops, toimpl
from sqlalchemy import MetaData
from sqlalchemy.schema import BLANK_SCHEMA

from scripts.database.registry import DATABASES, REPOSITORY_ROOT, metadata_for, migration_path


def comparison_metadata(database: str, schema: str) -> MetaData:
    """Copy logical models with reflection's actual table and FK schemas."""
    source = metadata_for(database)
    target = MetaData(naming_convention=source.naming_convention)
    normalized = None if schema == "public" else schema
    for table in source.sorted_tables:
        if table.schema != "public":
            raise RuntimeError("Models must use the logical public schema")
        table.to_metadata(
            target,
            schema=normalized,
            referred_schema_fn=lambda table, schema, fk, ref: (
                BLANK_SCHEMA if normalized is None else normalized
            ),
        )
    return target


def _translated_table(table, source, target):
    metadata = MetaData(naming_convention=table.metadata.naming_convention)
    # Alembic creates placeholder referenced tables. Copy them as well so
    # translated composite FK targets remain resolvable by the compiler.
    for related in table.metadata.tables.values():
        related.to_metadata(
            metadata,
            schema=target if related.schema == source else related.schema,
            referred_schema_fn=lambda table, schema, fk, ref: target if ref == source else ref,
        )
    actual = target if table.schema == source else table.schema
    key = f"{actual}.{table.name}" if actual else table.name
    return metadata.tables[key]


def _translated_operation(operation, source, target, migration_context):
    """Copy structured operation schemas; SQL text is deliberately untouched."""
    if isinstance(operation, ops.CreateTableOp) and operation.schema == source:
        table = operation.to_table(migration_context)
        result = ops.CreateTableOp.from_table(_translated_table(table, source, target))
        result.if_not_exists = operation.if_not_exists
        return result
    result = copy(operation)
    if hasattr(result, "schema") and result.schema == source:
        result.schema = target
    if hasattr(result, "kw"):
        result.kw = dict(result.kw)
        for name in ("schema", "source_schema", "referent_schema"):
            if name in result.kw and result.kw[name] == source:
                result.kw[name] = target
    if isinstance(operation, ops.AddColumnOp):
        # Use Alembic's own constructor to copy even a detached Column with
        # inline ForeignKey strings before translating its table and FK targets.
        table = ops.CreateTableOp(
            operation.table_name, [operation.column], schema=operation.schema
        ).to_table(migration_context)
        result.column = _translated_table(table, source, target).c[operation.column.name]
    if isinstance(operation, ops.BulkInsertOp):
        result.table = _translated_table(operation.table, source, target)
    reverse = getattr(operation, "_reverse", None)
    if reverse is not None:
        result._reverse = _translated_operation(reverse, source, target, migration_context)
    if isinstance(operation, ops.OpContainer):
        result.ops = list(operation.ops)
    return result


def _mapped_operation(operations: Operations, operation):
    target = operations.migration_context.opts.get("dxos_schema")
    if not target or target == "public":
        return operation
    return _translated_operation(operation, "public", target, operations.migration_context)


portable_revisions = Rewriter()


@portable_revisions.rewrites(ops.MigrateOperation)
def _canonicalize_revision(migration_context, revision, operation):
    """Keep new Git revisions portable across both deployment layouts.

    Autogenerate compares the physical namespace; authored operations retain
    logical public. The same runtime mapper resolves that logical schema when
    executing online or rendering offline, including generated downgrade ops.
    """
    physical = migration_context.opts.get("dxos_schema", "public")
    source = None if physical == "public" else physical
    return _translated_operation(operation, source, "public", migration_context)


# Public extension API, available since Alembic 1.17.2. Retain the native
# implementations and their dialect behavior. Each MigrationContext owns its
# mapping, so histories cannot leak a schema value into one another.
@Operations.implementation_for(ops.CreateTableOp, replace=True)
def _create_table(operations, operation):
    return toimpl.create_table(operations, _mapped_operation(operations, operation))


@Operations.implementation_for(ops.DropTableOp, replace=True)
def _drop_table(operations, operation):
    return toimpl.drop_table(operations, _mapped_operation(operations, operation))


@Operations.implementation_for(ops.CreateIndexOp, replace=True)
def _create_index(operations, operation):
    return toimpl.create_index(operations, _mapped_operation(operations, operation))


@Operations.implementation_for(ops.DropIndexOp, replace=True)
def _drop_index(operations, operation):
    return toimpl.drop_index(operations, _mapped_operation(operations, operation))


def _register_translated_operation(operation_class, native):
    @Operations.implementation_for(operation_class, replace=True)
    def translated(operations, operation):
        return native(operations, _mapped_operation(operations, operation))


# Future autogenerated revisions commonly alter columns/constraints as well
# as tables. Handle their structured schema arguments through the same mapper.
for _operation_class, _native in (
    (ops.AddColumnOp, toimpl.add_column),
    (ops.AlterColumnOp, toimpl.alter_column),
    (ops.DropColumnOp, toimpl.drop_column),
    (ops.CreatePrimaryKeyOp, toimpl.create_constraint),
    (ops.CreateUniqueConstraintOp, toimpl.create_constraint),
    (ops.CreateForeignKeyOp, toimpl.create_constraint),
    (ops.CreateCheckConstraintOp, toimpl.create_constraint),
    (ops.DropConstraintOp, toimpl.drop_constraint),
    (ops.CreateTableCommentOp, toimpl.create_table_comment),
    (ops.DropTableCommentOp, toimpl.drop_table_comment),
    (ops.RenameTableOp, toimpl.rename_table),
    (ops.BulkInsertOp, toimpl.bulk_insert),
):
    _register_translated_operation(_operation_class, _native)


def alembic_config(database: str) -> Config:
    if database not in DATABASES:
        raise ValueError("Unknown database")
    root = REPOSITORY_ROOT / "scripts/database/migrations"
    config = Config()
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(root).replace("%", "%%"))
    config.set_main_option(
        "version_locations", str(migration_path(database) / "versions").replace("%", "%%")
    )
    config.set_main_option("dxos_database", database)
    return config
