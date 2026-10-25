# SPDX-License-Identifier: GPL-3.0-or-later
"""Warehouse-owned confirmed preparation policy; absent means unknown, never zero."""
import sqlalchemy as sa
from alembic import op

revision = "warehouse_0005"
down_revision = "warehouse_0004"


def upgrade():
    schema = op.get_context().opts.get("dxos_schema", "public")
    op.add_column("warehouses", sa.Column("preparation_minutes", sa.Integer), schema=schema)
    op.add_column("warehouses", sa.Column("preparation_policy_revision", sa.Integer,
                                          nullable=False, server_default="0"), schema=schema)
    op.add_column("warehouses", sa.Column("preparation_confirmed_at", sa.DateTime(timezone=True)),
                  schema=schema)
    op.add_column("warehouses", sa.Column("preparation_confirmed_by", sa.String(255)), schema=schema)
    op.create_check_constraint("ck_warehouses_preparation_policy", "warehouses",
        "(preparation_policy_revision = 0 AND preparation_minutes IS NULL AND "
        "preparation_confirmed_at IS NULL AND preparation_confirmed_by IS NULL) OR "
        "(preparation_policy_revision > 0 AND preparation_minutes IS NOT NULL AND "
        "preparation_minutes BETWEEN 0 AND 10080 AND preparation_confirmed_at IS NOT NULL AND "
        "preparation_confirmed_by IS NOT NULL AND length(btrim(preparation_confirmed_by)) > 0)",
        schema=schema)
    op.execute(f'''CREATE VIEW "{schema}".v_handoff_preparation_policy AS
        SELECT org_id,id AS warehouse_id,preparation_minutes,preparation_policy_revision,
               preparation_confirmed_at,preparation_confirmed_by
        FROM "{schema}".warehouses WHERE status='ACTIVE' ''')


def downgrade():
    raise RuntimeError("Review published policy contracts before removing this source")
