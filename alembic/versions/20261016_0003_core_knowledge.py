# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
# Add/adopt the generic PostgreSQL Markdown and pgvector store.

from pathlib import Path

from alembic import context, op

revision = "0003_core_knowledge"
down_revision = "0002_api_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (Path(__file__).resolve().parents[1] / "sql" / "core_0003.sql").read_text("utf-8")
    if context.is_offline_mode():
        context.get_context().impl.static_output(sql)
    else:
        connection = op.get_bind()
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        connection.connection.dbapi_connection.run_async(lambda driver: driver.execute(sql))


def downgrade() -> None:
    # These tables may predate Alembic and contain published evidence. Never auto-drop them.
    raise RuntimeError("Core evidence is retained. A reviewed manual rollback is required.")
