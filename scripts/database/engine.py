"""Create engines with logical ``public`` tables mapped to their deployment schema."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from scripts.database.config import database_url, schema_for


def make_engine(database: str, **kwargs) -> Engine:
    """Keep immutable model definitions reusable in both deployment layouts.

    SQLAlchemy applies this map to schema-qualified Core/ORM expressions. Raw
    SQL strings must still quote/qualify their intended schema explicitly.
    Callers own the returned engine and must dispose it when finished.
    """
    schema = schema_for(database)
    engine = create_engine(database_url(database), **kwargs)
    if schema != "public":
        mapping = dict(engine.get_execution_options().get("schema_translate_map") or {})
        mapping["public"] = schema
        engine = engine.execution_options(schema_translate_map=mapping)
    return engine
