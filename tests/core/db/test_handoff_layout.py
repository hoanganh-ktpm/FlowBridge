import hashlib
import json
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from scripts.database.migration import alembic_config
from scripts.database.registry import DATABASES, REPOSITORY_ROOT, metadata_for, migration_path
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

FINGERPRINTS = json.loads(
    (REPOSITORY_ROOT / "tests/fixtures/database/handoff-fingerprints.json").read_text(
        encoding="utf-8"
    )
)
ADDITIVE = json.loads(
    (REPOSITORY_ROOT / "tests/fixtures/database/decision-workspace-fingerprints.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("database", ["sales", "warehouse", "logistics"])
def test_models_match_reviewed_handoff_and_additive_ddl(database):
    actual = {}
    for table in metadata_for(database).sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        ddl += "".join(
            sorted(
                str(CreateIndex(index).compile(dialect=postgresql.dialect()))
                for index in table.indexes
            )
        )
        actual[table.name] = hashlib.sha256(ddl.encode()).hexdigest()
    assert actual == {**FINGERPRINTS["tables"][database], **ADDITIVE["tables"].get(database, {})}


@pytest.mark.parametrize("database", DATABASES)
def test_migration_content_and_history_are_preserved(database):
    config = alembic_config(database)
    scripts = ScriptDirectory.from_config(config)
    heads = {
        "core": "core_0006",
        "sales": "sales_0005",
        "warehouse": "warehouse_0005",
        "logistics": "logistics_0004",
    }
    assert scripts.get_heads() == [heads[database]]
    for revision in scripts.walk_revisions():
        path = Path(revision.path)
        assert path.parent == migration_path(database) / "versions"
        digest = hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
        if path.name not in {"core_0004.py", "core_0005.py", "core_0006.py"}:
            expected = {**FINGERPRINTS["migrations"], **ADDITIVE["migrations"]}
            assert digest == expected[path.name]


@pytest.mark.parametrize("database", DATABASES)
def test_foreign_keys_stay_in_owned_metadata(database):
    metadata = metadata_for(database)
    for table in metadata.tables.values():
        assert table.schema == "public"
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.table.metadata is metadata


def test_core_fixtures_remain_drafts_without_live_permissions(monkeypatch):
    from scripts.database.fixtures.core import core_seed_rows

    monkeypatch.setenv("DXOS_LAYOUT", "databases")
    for database in DATABASES:
        monkeypatch.setenv(
            f"DXOS_{database.upper()}_URL",
            f"postgresql+psycopg://synthetic@localhost/{database}_db",
        )
    rows = core_seed_rows()
    assert sum(map(len, rows.values())) == 9
    assert all(row["status"] == "DRAFT" for row in rows["applications"])
    assert not rows["data_contract_versions"]
    assert not rows["publication_jobs"]
    assert not rows["principals"]
    assert not rows["access_grants"]
