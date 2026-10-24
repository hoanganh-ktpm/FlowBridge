# SPDX-License-Identifier: GPL-3.0-or-later
"""Confirmed demand by selected fulfillment warehouse for supply planning."""
from alembic import op

revision = "sales_0005"
down_revision = "sales_0004"


def upgrade():
    s = '"' + op.get_context().opts.get('dxos_schema', 'public') + '"'
    op.execute(f'''CREATE VIEW {s}.v_planning_demand_daily AS
        SELECT o.org_id,o.fulfillment_warehouse_ref AS warehouse_id,l.sku,l.uom,
               (o.confirmed_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS day,
               SUM(l.ordered_quantity) AS confirmed_quantity
        FROM {s}.sales_orders o JOIN {s}.sales_order_lines l
          ON (l.org_id,l.order_id)=(o.org_id,o.id)
        WHERE o.confirmed_at IS NOT NULL
        GROUP BY o.org_id,o.fulfillment_warehouse_ref,l.sku,l.uom,
                 (o.confirmed_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date''')


def downgrade():
    raise RuntimeError("Review published contracts before removing warehouse-scoped demand")
