# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import io
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_chat_advances_published_knowledge_revision_without_reapplying_it(monkeypatch):
    monkeypatch.setenv(
        "FLOWBRIDGE_DATABASE_URL", "postgresql+asyncpg://offline:offline@localhost/offline"
    )
    config = Config(
        str(Path(__file__).resolve().parents[2] / "alembic.ini"), output_buffer=io.StringIO()
    )
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["0005_chat_projection"]
    assert scripts.get_revision("0005_chat_projection").down_revision == "0004_knowledge_manager"
    command.upgrade(config, "0004_knowledge_manager:head", sql=True)
    sql = config.output_buffer.getvalue()
    for table in ("chat_sessions", "chat_turns"):
        assert f"CREATE TABLE {table}" in sql
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in sql
    assert "UNIQUE (owner_id, idempotency_key)" in sql
    assert "CREATE TABLE core.knowledge" not in sql
    assert "0005_chat_projection" in sql
