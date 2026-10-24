# SPDX-License-Identifier: GPL-3.0-or-later
"""Own schema and three JSONB result/configuration tables, no source data copies."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "workspace_0001"
down_revision = None


def upgrade():
    op.execute("CREATE SCHEMA data_workspace AUTHORIZATION workspace_owner")
    for name in ("analysis_configs", "forecast_runs", "simulation_runs"):
        op.create_table(
            name,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("org_id", sa.Uuid(), nullable=False),
            sa.Column("owner_id", sa.Uuid(), nullable=False),
            sa.Column("payload", JSONB(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.CheckConstraint("jsonb_typeof(payload) = 'object'"),
            schema="data_workspace",
        )
        op.create_index(f"ix_{name}_owner_time", name,
                        ["org_id", "owner_id", "created_at"], schema="data_workspace")


def downgrade():
    raise RuntimeError("Destructive downgrade requires a separately reviewed recovery plan")
