# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete location/product catalogs, including rows without inventory balances."""
from alembic import op

revision = "warehouse_0006"
down_revision = "warehouse_0005"


def upgrade():
    s = '"' + op.get_context().opts.get('dxos_schema', 'public') + '"'
    op.execute(f'''CREATE VIEW {s}.v_location_register AS
        SELECT org_id,id AS warehouse_id,warehouse_code, name AS warehouse_name,status
        FROM {s}.warehouses''')
    op.execute(f'''CREATE VIEW {s}.v_product_register AS
        SELECT org_id,id AS product_id,sku,name AS product_name,uom,status
        FROM {s}.products''')


def downgrade():
    raise RuntimeError("Review dependent published contracts before removing register views")
