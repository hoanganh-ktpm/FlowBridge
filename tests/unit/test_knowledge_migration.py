# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]


def migration_config(monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(
        "FLOWBRIDGE_DATABASE_URL", "postgresql+asyncpg://offline:offline@localhost/offline"
    )
    return Config(str(ROOT / "alembic.ini"), output_buffer=io.StringIO())


def test_offline_upgrade_includes_knowledge_store_and_advances_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = migration_config(monkeypatch)
    command.upgrade(config, "0002_api_idempotency:head", sql=True)
    sql = config.output_buffer.getvalue()
    assert "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions" in sql
    for table in ("documents", "document_versions", "document_chunks"):
        assert f"CREATE TABLE IF NOT EXISTS core.{table}" in sql
        assert f"ALTER TABLE core.{table} ENABLE ROW LEVEL SECURITY" in sql
    assert "CREATE TRIGGER trg_guard_published_document_versions" in sql
    assert "CREATE TRIGGER trg_guard_document_chunk_source" in sql
    assert "SET version_num='0003_core_knowledge'" in sql
    assert "WHERE alembic_version.version_num = '0002_api_idempotency'" in sql


def test_knowledge_downgrade_refuses_to_drop_published_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = migration_config(monkeypatch)
    with pytest.raises(RuntimeError, match="reviewed manual rollback"):
        command.downgrade(config, "0003_core_knowledge:0002_api_idempotency", sql=True)
    assert "DROP TABLE" not in config.output_buffer.getvalue()
