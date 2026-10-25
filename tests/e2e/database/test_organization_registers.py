# SPDX-License-Identifier: GPL-3.0-or-later
"""Register migrations include current owner data, not only planning-ready rows."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from scripts.database.registry import metadata_for

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


def test_warehouse_registers_include_empty_location_and_inactive_unstocked_product(databases):
    tables = metadata_for("warehouse").tables
    with databases["warehouse"].connect() as conn:
        tx = conn.begin()
        try:
            org = conn.scalar(sa.select(tables["public.warehouses"].c.org_id).limit(1))
            warehouse, product = uuid4(), uuid4()
            conn.execute(
                tables["public.warehouses"]
                .insert()
                .values(
                    id=warehouse, org_id=org, warehouse_code="EMPTY-REGISTER", name="Kho chưa nhập"
                )
            )
            conn.execute(
                tables["public.products"]
                .insert()
                .values(
                    id=product,
                    org_id=org,
                    sku="UNSTOCKED-REGISTER",
                    name="Chưa nhập kho",
                    uom="cái",
                    status="INACTIVE",
                )
            )
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM v_location_register WHERE warehouse_id=:id"),
                    {"id": warehouse},
                )
                == 1
            )
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM v_product_register WHERE product_id=:id"),
                    {"id": product},
                )
                == 1
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM inventory_balances "
                        "WHERE product_id=:id OR warehouse_id=:wh"
                    ),
                    {"id": product, "wh": warehouse},
                )
                == 0
            )
            columns = {c["name"] for c in sa.inspect(conn).get_columns("v_product_register")}
            assert "unit_cost" not in columns
        finally:
            tx.rollback()


def test_sales_register_has_completed_cancelled_and_draft_without_lines(databases):
    table = metadata_for("sales").tables["public.sales_orders"]
    with databases["sales"].connect() as conn:
        tx = conn.begin()
        try:
            source = conn.execute(sa.select(table).limit(1)).mappings().one()
            identifier = uuid4()
            conn.execute(
                table.insert().values(
                    id=identifier,
                    org_id=source["org_id"],
                    customer_id=source["customer_id"],
                    order_number="EMPTY-DRAFT-REGISTER",
                    status="DRAFT",
                    created_by_subject="disposable-register-test",
                )
            )
            assert (
                conn.scalar(
                    sa.text("SELECT line_count FROM v_order_register WHERE order_id=:id"),
                    {"id": identifier},
                )
                == 0
            )
            states = set(conn.scalars(sa.text("SELECT DISTINCT status FROM v_order_register")))
            assert {"COMPLETED", "CANCELLED", "DRAFT"} <= states
            assert conn.scalar(sa.text("SELECT count(*) FROM v_order_register")) == conn.scalar(
                sa.select(sa.func.count()).select_from(table)
            )
            columns = {c["name"] for c in sa.inspect(conn).get_columns("v_order_register")}
            assert {"recipient_phone", "delivery_address_snapshot", "recipient_name"}.isdisjoint(
                columns
            )
        finally:
            tx.rollback()
