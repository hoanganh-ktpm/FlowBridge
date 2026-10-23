"""Four isolated module schemas can coexist with unrelated Supabase data.

These tests create one randomly named disposable database on the explicit test
server. They never read a .env target URL or alter a pre-existing database.
"""

import pytest
from scripts.database.config import schema_for, validate_distinct_targets
from scripts.database.registry import DATABASES


def configure_shared_target(monkeypatch):
    monkeypatch.setenv("DXOS_LAYOUT", "schemas")
    monkeypatch.setenv("DXOS_DATABASE_URL", "postgresql+psycopg://tester@127.0.0.1:55439/postgres")
    for name in DATABASES:
        monkeypatch.delenv(f"DXOS_{name.upper()}_URL", raising=False)
        monkeypatch.delenv(f"DXOS_{name.upper()}_SCHEMA", raising=False)


def test_shared_layout_accepts_distinct_namespaces(monkeypatch):
    configure_shared_target(monkeypatch)
    validate_distinct_targets()
    assert [schema_for(name) for name in DATABASES] == [
        "sales",
        "warehouse",
        "logistics",
        "dxos_core",
    ]


def test_shared_layout_rejects_namespace_aliases_even_for_one_target(monkeypatch):
    configure_shared_target(monkeypatch)
    monkeypatch.setenv("DXOS_SALES_SCHEMA", "same_namespace")
    monkeypatch.setenv("DXOS_CORE_SCHEMA", "same_namespace")
    with pytest.raises(ValueError, match="different schema"):
        validate_distinct_targets(("core",))


@pytest.mark.parametrize(
    "unsafe",
    [
        "public",
        "auth",
        "storage",
        "pg_catalog",
        "sales;DROP SCHEMA public",
        "MixedCase",
    ],
)
def test_shared_layout_rejects_managed_or_unsafe_namespaces(monkeypatch, unsafe):
    configure_shared_target(monkeypatch)
    monkeypatch.setenv("DXOS_SALES_SCHEMA", unsafe)
    with pytest.raises(ValueError, match="SQL identifier"):
        validate_distinct_targets()


def test_shared_layout_rejects_different_supabase_projects_on_same_pooler(monkeypatch):
    configure_shared_target(monkeypatch)
    monkeypatch.setenv(
        "DXOS_DATABASE_URL",
        "postgresql+psycopg://postgres.project_a@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres",
    )
    monkeypatch.setenv(
        "DXOS_CORE_URL",
        "postgresql+psycopg://postgres.project_b@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres",
    )
    with pytest.raises(ValueError, match="same physical database"):
        validate_distinct_targets()
