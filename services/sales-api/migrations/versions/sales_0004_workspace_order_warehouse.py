# SPDX-License-Identifier: GPL-3.0-or-later
"""Expose the owning order's selected warehouse for governed planning."""
from alembic import op

revision = "sales_0004"
down_revision = "sales_0003"


def upgrade():
    s = '"' + op.get_context().opts.get('dxos_schema', 'public') + '"'
    op.execute(f'''CREATE OR REPLACE VIEW {s}.v_open_orders AS
        SELECT o.org_id,o.id AS order_id,o.order_number,o.status,o.confirmed_at,
               o.requested_handoff_at,l.id AS order_line_id,l.product_ref,l.sku,
               l.uom,l.ordered_quantity,o.fulfillment_warehouse_ref AS warehouse_id
        FROM {s}.sales_orders o JOIN {s}.sales_order_lines l
          ON (l.org_id,l.order_id)=(o.org_id,o.id)
        WHERE o.confirmed_at IS NOT NULL AND o.status NOT IN ('COMPLETED','CANCELLED')''')


def downgrade():
    raise RuntimeError("Review dependent published contracts before removing a view column")
