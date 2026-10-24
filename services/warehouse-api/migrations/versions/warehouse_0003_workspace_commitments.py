# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only order commitment facts; no stock, holds or transactions are changed."""
from alembic import op

revision = "warehouse_0003"
down_revision = "warehouse_0002"


def upgrade():
    s = '"' + op.get_context().opts.get('dxos_schema', 'public') + '"'
    op.execute(f'''CREATE VIEW {s}.v_order_commitments AS
        SELECT r.org_id,r.sales_order_ref AS order_id,
               l.sales_order_line_ref AS order_line_id,r.warehouse_id,p.sku,p.uom,
               SUM(l.dispatched_quantity) AS dispatched_quantity,
               SUM(CASE WHEN r.status IN ('HELD','PARTIALLY_DISPATCHED')
                   THEN l.allocated_quantity-l.released_quantity-l.dispatched_quantity
                   ELSE 0 END) AS held_quantity
        FROM {s}.reservations r JOIN {s}.reservation_lines l
          ON (l.org_id,l.reservation_id)=(r.org_id,r.id)
        JOIN {s}.products p ON (p.org_id,p.id)=(l.org_id,l.product_id)
        GROUP BY r.org_id,r.sales_order_ref,l.sales_order_line_ref,r.warehouse_id,p.sku,p.uom''')


def downgrade():
    raise RuntimeError("Review published contracts before removing the commitment view")
