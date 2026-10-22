from typing import Any

from .schemas import TableRef


def postgres_source_args(name: str, environment_name: str) -> dict[str, Any]:
    return {
        "name": name,
        "configuration": {
            "connection_info": {
                "database_url": {"from_env": environment_name},
                "use_prepared_statements": False,
                "isolation_level": "read-committed",
                "pool_settings": {"max_connections": 5},
            }
        },
        "customization": {"root_fields": {"namespace": name}, "type_names": {"prefix": f"{name}_"}},
        "replace_configuration": False,
    }


def relationship_operation(
    source: str,
    table: TableRef,
    name: str,
    cardinality: str,
    target_source: str,
    target_table: TableRef,
    mapping: dict[str, str],
    strategy: str,
    fk_columns: tuple[str, ...] = (),
) -> dict[str, Any]:
    args: dict[str, Any] = {"source": source, "table": table.payload(), "name": name}
    if strategy == "remote":
        args["definition"] = {
            "to_source": {
                "relationship_type": cardinality,
                "source": target_source,
                "table": target_table.payload(),
                "field_mapping": mapping,
            }
        }
        operation = "pg_create_remote_relationship"
    else:
        operation = f"pg_create_{cardinality}_relationship"
        if strategy == "fk":
            constraint: Any = list(fk_columns)
            if cardinality == "array":
                constraint = {"table": target_table.payload(), "columns": list(fk_columns)}
            args["using"] = {"foreign_key_constraint_on": constraint}
        else:
            args["using"] = {
                "manual_configuration": {
                    "remote_table": target_table.payload(),
                    "column_mapping": mapping,
                }
            }
    return {"type": operation, "version": 1, "args": args}
