"""Add confirmed demand and open orders products

Revision ID: sales_0002
Revises: sales_0001
"""
from collections.abc import Sequence
from alembic import op


revision: str = 'sales_0002'
down_revision: str | Sequence[str] | None = 'sales_0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = '"' + op.get_context().opts.get('dxos_schema', 'public') + '"'
    op.execute(f'''CREATE VIEW {s}.v_confirmed_demand_daily AS
        SELECT o.org_id, l.sku,
               (o.confirmed_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS day,
               SUM(l.ordered_quantity) AS confirmed_quantity
        FROM {s}.sales_orders o JOIN {s}.sales_order_lines l
          ON (l.org_id,l.order_id)=(o.org_id,o.id)
        WHERE o.confirmed_at IS NOT NULL
        GROUP BY o.org_id,l.sku,(o.confirmed_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date''')
    # Immutable confirmed snapshots represent gross demand, including subsequent
    # cancellations. Cancellation is a separate lifecycle fact. No event joins.
    op.execute(f'''CREATE VIEW {s}.v_open_orders AS
        SELECT o.org_id,o.id AS order_id,o.order_number,o.status,o.confirmed_at,
               o.requested_handoff_at,l.id AS order_line_id,l.product_ref,l.sku,
               l.uom,l.ordered_quantity
        FROM {s}.sales_orders o JOIN {s}.sales_order_lines l
          ON (l.org_id,l.order_id)=(o.org_id,o.id)
        WHERE o.confirmed_at IS NOT NULL AND o.status NOT IN ('COMPLETED','CANCELLED')''')


def downgrade() -> None:
    s = '"' + op.get_context().opts.get('dxos_schema', 'public') + '"'
    op.execute(f'DROP VIEW {s}.v_open_orders')
    op.execute(f'DROP VIEW {s}.v_confirmed_demand_daily')
