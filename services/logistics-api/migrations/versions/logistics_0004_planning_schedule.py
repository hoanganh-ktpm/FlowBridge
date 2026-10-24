# SPDX-License-Identifier: GPL-3.0-or-later
"""Expose order-specific scheduling evidence without shipping-label PII."""
from alembic import op

revision = "logistics_0004"
down_revision = "logistics_0003"


def upgrade():
    s = '"' + op.get_context().opts.get('dxos_schema', 'public') + '"'
    op.execute(f'''CREATE VIEW {s}.v_handoff_schedule AS
        SELECT org_id,id AS shipment_id,shipment_number,sales_order_ref AS order_id,
               sales_order_number AS order_number,warehouse_ref AS warehouse_id,
               carrier_code_snapshot AS carrier_code,carrier_name_snapshot AS carrier_name,
               planned_handoff_at,scheduled_handoff_at,accepted_at,handed_over_at,status
        FROM {s}.shipments
        WHERE status <> 'DRAFT' ''')


def downgrade():
    raise RuntimeError("Review published contracts before removing the schedule interface")
