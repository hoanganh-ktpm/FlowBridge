"""Four revision histories, isolated by database or owned PostgreSQL schema."""

from alembic import context
from scripts.database.config import database_url, load_configuration, schema_for
from scripts.database.engine import make_engine
from scripts.database.migration import comparison_metadata, portable_revisions
from scripts.database.registry import metadata_for
from sqlalchemy import pool
from sqlalchemy.schema import CreateSchema

config = context.config
load_configuration()
database = config.get_main_option("dxos_database")
schema = schema_for(database)
target_metadata = comparison_metadata(database, schema)
owned_tables = {table.name for table in metadata_for(database).tables.values()}


def include_name(name, type_, parent_names):
    """Never reflect Supabase internals, legacy public, or unowned tables."""
    normalized = None if schema == "public" else schema
    if type_ == "schema":
        return name == normalized
    if type_ == "table":
        return parent_names.get("schema_name") == normalized and name in owned_tables
    return True


def configure(connection=None):
    options = {
        "target_metadata": target_metadata,
        "include_schemas": schema != "public",
        "include_name": include_name,
        "compare_type": True,
        "compare_server_default": True,
        "version_table": "alembic_version",
        "version_table_schema": schema,
        "dxos_schema": schema,
        "process_revision_directives": portable_revisions,
        "include_object": lambda obj, name, type_, reflected, compare_to: (
            not (type_ == "table" and name == "alembic_version")
        ),
        "transaction_per_migration": True,
    }
    if connection is None:
        context.configure(
            url=database_url(database),
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            **options,
        )
    else:
        context.configure(connection=connection, **options)
    with context.begin_transaction():
        if connection is None:
            context.execute("SET search_path TO public")
            if schema != "public":
                context.execute(CreateSchema(schema, if_not_exists=True))
        context.run_migrations()


if context.is_offline_mode():
    configure()
elif config.attributes.get("connection") is not None:
    configure(config.attributes["connection"])
else:
    engine = make_engine(database, poolclass=pool.NullPool)
    with engine.connect() as connection:
        # Keep custom schemas explicit during reflection. Putting the target in
        # search_path omits its schema on FK references and causes false drift.
        connection.exec_driver_sql("SET search_path TO public")
        connection.commit()
        configure(connection)
    engine.dispose()
