import pytest
from sqlalchemy import Integer, Text

from app.core.data_sources.discovery import (
    DiscoverySource,
    SourceSnapshot,
    canonical_type,
    fk_suggestions,
    inspect_schema,
)
from app.core.hasura_provisioner.schemas import TableRef


class InspectorFixture:
    def get_schema_names(self):
        return ["public"]

    def get_table_names(self, **kwargs):
        return ["orders", "lines"]

    def get_view_names(self, **kwargs):
        return ["stock_available"]

    def get_columns(self, name, **kwargs):
        return [
            {"name": "id", "type": Integer(), "nullable": False},
            {"name": "sku", "type": Text(), "nullable": False},
        ]

    def get_pk_constraint(self, name, **kwargs):
        return {"constrained_columns": [] if name == "stock_available" else ["id", "sku"]}

    def get_unique_constraints(self, name, **kwargs):
        return [] if name == "stock_available" else [{"column_names": ["sku"]}]

    def get_foreign_keys(self, name, **kwargs):
        assert kwargs["postgresql_ignore_search_path"]
        return (
            []
            if name != "lines"
            else [
                {
                    "name": "lines_order_fk",
                    "constrained_columns": ["id", "sku"],
                    "referred_schema": "public",
                    "referred_table": "orders",
                    "referred_columns": ["id", "sku"],
                }
            ]
        )


def test_inspector_reads_only_requested_objects_and_preserves_composite_keys():
    source = DiscoverySource(
        key="test",
        host="localhost",
        port=5432,
        database="test",
        username="reader",
        ssl_mode="require",
        secret_ref="reader",
        objects=tuple(
            TableRef(schema="public", name=name)
            for name in ("orders", "lines", "stock_available", "missing")
        ),
    )
    snapshot = inspect_schema(None, source, lambda connection: InspectorFixture())
    assert len(snapshot.objects) == 3
    assert snapshot.find(TableRef(schema="public", name="missing")) is None
    assert snapshot.objects[0].primary_key == ("id", "sku")
    view = snapshot.find(TableRef(schema="public", name="stock_available"))
    assert not view.primary_key and not view.foreign_keys and not view.unique_constraints
    suggestions = fk_suggestions(snapshot)
    assert len(suggestions) == 1
    assert suggestions[0]["mapping"] == {"id": "id", "sku": "sku"}
    assert suggestions[0]["confirmed"] is False
    assert SourceSnapshot.model_validate(snapshot.model_dump(by_alias=True)) == snapshot


@pytest.mark.parametrize(
    ("pg", "expected"),
    [
        ("VARCHAR(100)", "text"),
        ("INTEGER", "integer"),
        ("INT8", "bigint"),
        ("TIMESTAMP WITH TIME ZONE", "timestamptz"),
        ("TIMESTAMP(6) WITH TIME ZONE", "timestamptz"),
        ("NUMERIC(10,2)", "numeric"),
    ],
)
def test_type_normalization(pg, expected):
    assert canonical_type(pg) == expected
