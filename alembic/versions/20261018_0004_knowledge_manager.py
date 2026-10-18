# SPDX-License-Identifier: GPL-3.0-or-later
# Add the durable Markdown manager without rewriting published evidence.

from pathlib import Path

from alembic import context, op

revision = "0004_knowledge_manager"
down_revision = "0003_core_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (Path(__file__).resolve().parents[1] / "sql/core_0004.sql").read_text("utf-8")
    if context.is_offline_mode():
        context.get_context().impl.static_output(sql)
    else:
        op.get_bind().connection.dbapi_connection.run_async(lambda driver: driver.execute(sql))


def downgrade() -> None:
    raise RuntimeError(
        "Knowledge jobs and evidence are retained; reviewed manual rollback required"
    )
