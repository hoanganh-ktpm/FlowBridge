"""The demonstration ends at physical handoff to a third-party carrier.

SQL CHECK constraints guard local row evidence. Cross-database projections and
aggregate quantities are checked by the read-only audit, not by an invented
cross-database foreign key or by treating a shipping label as customer receipt.
"""

from contextlib import ExitStack

import pytest
from scripts.database.registry import DATABASES, metadata_for
from scripts.database.verify import verify_connections, verify_demo
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


def table(domain, name):
    return metadata_for(domain).tables[f"public.{name}"]


@pytest.mark.parametrize(
    ("domain", "name", "before", "after"),
    [
        ("sales", "sales_orders", "RESERVED", "DELIVERED"),
        ("warehouse", "dispatches", "PREPARED", "IN_TRANSIT"),
        ("warehouse", "dispatches", "PREPARED", "DELIVERED"),
        ("logistics", "shipments", "ACCEPTED", "IN_TRANSIT"),
        ("logistics", "shipments", "ACCEPTED", "DELIVERED"),
        ("logistics", "shipments", "ACCEPTED", "RETURNED"),
    ],
)
def test_customer_delivery_lifecycle_is_outside_database_scope(
    databases, domain, name, before, after
):
    target = table(domain, name)
    with databases[domain].connect() as connection:
        transaction = connection.begin()
        try:
            assert (
                connection.scalar(select(target.c.id).where(target.c.status == before).limit(1))
                is not None
            )
            with pytest.raises(IntegrityError) as error:
                connection.execute(
                    target.update().where(target.c.status == before).values(status=after)
                )
            assert error.value.orig.sqlstate == "23514"
            assert error.value.orig.diag.constraint_name == f"ck_{name}_status"
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("carrier_receiver_name", None),
        ("carrier_receiver_name", "   "),
        ("carrier_name_snapshot", None),
        ("handed_over_by_subject", None),
        ("handoff_event_id", None),
        ("logistics_acceptance_ref", None),
    ],
)
def test_physical_handoff_requires_carrier_and_warehouse_evidence(databases, field, value):
    target = table("warehouse", "dispatches")
    with databases["warehouse"].connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError) as error:
                connection.execute(
                    target.update().where(target.c.status == "HANDED_OVER").values(**{field: value})
                )
            assert error.value.orig.sqlstate == "23514"
            assert error.value.orig.diag.constraint_name == "ck_dispatches_handoff_evidence"
        finally:
            transaction.rollback()


def test_prepared_and_accepted_are_not_completed_physical_handoffs(databases):
    dispatches = table("warehouse", "dispatches")
    dispatch_lines = table("warehouse", "dispatch_lines")
    movements = table("warehouse", "stock_movements")
    shipments = table("logistics", "shipments")
    shipment_lines = table("logistics", "shipment_lines")
    orders = table("sales", "sales_orders")

    with databases["warehouse"].connect() as connection:
        prepared = (
            connection.execute(select(dispatches).where(dispatches.c.status == "PREPARED"))
            .mappings()
            .one()
        )
        assert prepared["handed_over_at"] is None
        movement = connection.scalar(
            select(movements.c.id)
            .join(dispatch_lines, movements.c.dispatch_line_id == dispatch_lines.c.id)
            .where(dispatch_lines.c.dispatch_id == prepared["id"])
            .limit(1)
        )
        assert movement is None

    with databases["logistics"].connect() as connection:
        accepted = (
            connection.execute(
                select(shipments).where(shipments.c.warehouse_dispatch_ref == prepared["id"])
            )
            .mappings()
            .one()
        )
        assert accepted["status"] == "ACCEPTED"
        assert accepted["handed_over_at"] is None
        quantities = connection.scalars(
            select(shipment_lines.c.handed_over_quantity).where(
                shipment_lines.c.shipment_id == accepted["id"]
            )
        ).all()
        assert quantities and all(quantity == 0 for quantity in quantities)

    with databases["sales"].connect() as connection:
        order = (
            connection.execute(select(orders).where(orders.c.id == accepted["sales_order_ref"]))
            .mappings()
            .one()
        )
        assert order["status"] == "FULFILLING"
        assert order["completed_at"] is None

    result = verify_demo()
    assert result["scope_end"] == "WAREHOUSE_PHYSICAL_HANDOFF_TO_CARRIER"
    assert result["customer_delivery_claimed"] is False
    assert result["handed_over_dispatches"] == 2
    assert result["prepared_dispatches"] == 1


def test_audit_rejects_completed_sales_without_full_carrier_handoff(databases):
    orders = table("sales", "sales_orders")
    # A row-local CHECK cannot sum physical lines in another database. The
    # integration audit must reject this false completion even with a timestamp.
    with ExitStack() as stack:
        connections = {
            domain: stack.enter_context(databases[domain].connect()) for domain in DATABASES
        }
        for connection in connections.values():
            transaction = connection.begin()
            stack.callback(transaction.rollback)
        changed = connections["sales"].execute(
            orders.update()
            .where(orders.c.order_number == "SO-2026-0102")
            .values(status="COMPLETED", completed_at=orders.c.updated_at)
        )
        assert changed.rowcount == 1
        with pytest.raises(RuntimeError, match="full physical line handoff"):
            verify_connections(connections)
