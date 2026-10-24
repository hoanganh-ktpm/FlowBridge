# SPDX-License-Identifier: GPL-3.0-or-later
"""Publish safe product/location labels and accepted posted receipts for planning."""
from alembic import op

revision = "warehouse_0004"
down_revision = "warehouse_0003"


def upgrade():
    s = '"' + op.get_context().opts.get('dxos_schema', 'public') + '"'
    op.execute(f'''CREATE VIEW {s}.v_planning_catalog AS
        SELECT b.org_id,b.warehouse_id,p.id AS product_id,p.sku,p.name AS product_name,
               p.uom,p.status AS product_status,w.warehouse_code,w.name AS warehouse_name
        FROM {s}.inventory_balances b JOIN {s}.products p
          ON (p.org_id,p.id)=(b.org_id,b.product_id)
        JOIN {s}.warehouses w ON (w.org_id,w.id)=(b.org_id,b.warehouse_id)''')
    op.execute(f'''CREATE VIEW {s}.v_receipts_daily AS
        SELECT h.org_id,h.warehouse_id,p.sku,p.uom,
               (h.received_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS day,
               SUM(l.accepted_quantity) AS received_quantity
        FROM {s}.goods_receipts h JOIN {s}.goods_receipt_lines l
          ON (l.org_id,l.receipt_id)=(h.org_id,h.id)
        JOIN {s}.products p ON (p.org_id,p.id)=(l.org_id,l.product_id)
        WHERE h.status='POSTED'
        GROUP BY h.org_id,h.warehouse_id,p.sku,p.uom,
                 (h.received_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date''')


def downgrade():
    raise RuntimeError("Review published contracts before removing planning read interfaces")
