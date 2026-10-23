import pytest
from scripts.database.config import validate_distinct_targets
from scripts.database.registry import DATABASES


@pytest.fixture(autouse=True)
def explicit_layout(monkeypatch):
    monkeypatch.setenv("DXOS_LAYOUT", "databases")
    monkeypatch.delenv("DXOS_DATABASE_URL", raising=False)


def test_single_target_still_rejects_configured_database_alias(monkeypatch):
    for name in DATABASES:
        monkeypatch.delenv(f"DXOS_{name.upper()}_URL", raising=False)
    monkeypatch.setenv("DXOS_SALES_URL", "postgresql+psycopg://test@localhost:5432/shared")
    monkeypatch.setenv("DXOS_CORE_URL", "postgresql+psycopg://test@localhost:5432/shared")
    with pytest.raises(ValueError, match="different physical database"):
        validate_distinct_targets(("core",))


def test_single_target_does_not_require_unconfigured_databases(monkeypatch):
    for name in DATABASES:
        monkeypatch.delenv(f"DXOS_{name.upper()}_URL", raising=False)
    monkeypatch.setenv("DXOS_CORE_URL", "postgresql+psycopg://test@localhost:5432/dxos_core_db")
    validate_distinct_targets(("core",))
