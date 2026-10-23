from io import StringIO
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from scripts.database.migration import alembic_config
from scripts.database.realign import require_core_target

from app.core.db.models import CoreBase

EXPECTED = {
    "principals",
    "departments",
    "department_memberships",
    "applications",
    "data_sources",
    "data_products",
    "data_contract_versions",
    "access_requests",
    "access_grants",
    "publication_jobs",
    "audit_events",
    "core_command_receipts",
}


def test_core_is_single_enterprise_p0():
    assert {table.name for table in CoreBase.metadata.tables.values()} == EXPECTED
    assert all("org_id" not in table.c for table in CoreBase.metadata.tables.values())
    assert "owner_department_id" not in CoreBase.metadata.tables["public.data_products"].c


def test_core_foreign_keys_pin_contract_to_product():
    metadata = CoreBase.metadata
    for name in ("access_requests", "access_grants"):
        assert any(
            set(constraint.column_keys) == {"contract_version_id", "data_product_id"}
            for constraint in metadata.tables["public." + name].foreign_key_constraints
        )


def test_core_history_renders_without_database(monkeypatch):
    monkeypatch.setenv("DXOS_LAYOUT", "databases")
    monkeypatch.setenv("DXOS_CORE_URL", "postgresql+psycopg://synthetic@localhost/dxos_core_db")
    config = alembic_config("core")
    config.output_buffer = StringIO()
    command.upgrade(config, "head", sql=True)
    ddl = config.output_buffer.getvalue()
    assert "CREATE TABLE public.principals" in ddl
    assert "CREATE TABLE public.core_command_receipts" in ddl
    assert "fk_product_active_contract" in ddl
    assert "DROP SCHEMA" not in ddl
    assert "CASCADE" not in ddl
    assert "core_0004" in ddl


@pytest.mark.parametrize(
    "confirmation", ["sales_db", "warehouse_db", "logistics_db", "postgres", ""]
)
def test_reset_refuses_other_databases(confirmation):
    with pytest.raises(ValueError):
        require_core_target(confirmation)


def test_reset_refuses_alias_with_domain_database(monkeypatch):
    monkeypatch.setenv("DXOS_LAYOUT", "databases")
    monkeypatch.setenv("DXOS_CORE_URL", "postgresql+psycopg://synthetic@localhost/warehouse_db")
    monkeypatch.setenv(
        "DXOS_WAREHOUSE_URL", "postgresql+psycopg://synthetic@localhost/warehouse_db"
    )
    with pytest.raises(ValueError):
        require_core_target("dxos_core_db")


def test_populated_core_requires_explicit_reset_permission(monkeypatch):
    module = ScriptDirectory.from_config(alembic_config("core")).get_revision("core_0004").module
    from sqlalchemy.dialects.postgresql import dialect

    connection = SimpleNamespace(
        dialect=dialect(), execute=lambda statement: None, scalar=lambda statement: True
    )
    context = SimpleNamespace(as_sql=False, opts={}, config=SimpleNamespace(attributes={}))
    monkeypatch.setattr(module.op, "get_context", lambda: context)
    monkeypatch.setattr(module.op, "get_bind", lambda: connection)
    with pytest.raises(RuntimeError, match="Core contains metadata"):
        module.require_reset_permission(["principals"])
    context.config.attributes["allow_core_data_reset"] = True
    module.require_reset_permission(["principals"])
