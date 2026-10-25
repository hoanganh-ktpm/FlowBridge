# SPDX-License-Identifier: GPL-3.0-or-later
"""Organization-scoped order registers, independent of planning/status windows."""
from alembic import op

revision = "sales_0006"
down_revision = "sales_0005"


def upgrade():
    s = '"' + op.get_context().opts.get('dxos_schema', 'public') + '"'
    op.execute(f'''CREATE VIEW {s}.v_order_register AS
        SELECT o.org_id,o.id AS order_id,o.order_number,o.status,
               o.fulfillment_warehouse_ref AS warehouse_id,o.created_at,o.confirmed_at,
               o.requested_handoff_at,o.completed_at,o.cancelled_at,
               (SELECT COUNT(*) FROM {s}.sales_order_lines l
                WHERE (l.org_id,l.order_id)=(o.org_id,o.id)) AS line_count
        FROM {s}.sales_orders o''')
    op.execute(f'''CREATE VIEW {s}.v_order_register_lines AS
        SELECT l.org_id,l.order_id,l.id AS order_line_id,l.line_no,l.sku,
               l.product_name_snapshot AS product_name,l.uom,l.ordered_quantity
        FROM {s}.sales_order_lines l''')


def downgrade():
    raise RuntimeError("Review dependent published contracts before removing register views")
