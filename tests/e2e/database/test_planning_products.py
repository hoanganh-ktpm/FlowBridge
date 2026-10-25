from uuid import uuid4

import pytest
import sqlalchemy as sa
from scripts.database.registry import metadata_for
from tests.e2e.database.test_postgresql import clone_row, reject, table

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


def test_six_product_views_and_gross_confirmed_demand(databases):
    expected = {
        "sales": {"v_confirmed_demand_daily", "v_open_orders", "v_planning_demand_daily",
                  "v_order_register", "v_order_register_lines"},
        "warehouse": {
            "v_stock_availability",
            "v_inbound_schedule",
            "v_order_commitments",
            "v_planning_catalog",
            "v_receipts_daily",
            "v_handoff_preparation_policy",
            "v_location_register",
            "v_product_register",
        },
        "logistics": {"v_handoff_capacity", "v_accepted_shipments_daily", "v_handoff_schedule"},
    }
    for domain, views in expected.items():
        assert set(sa.inspect(databases[domain]).get_view_names()) == views
    with databases["sales"].connect() as conn:
        gross = conn.scalar(sa.text("SELECT sum(confirmed_quantity) FROM v_confirmed_demand_daily"))
        actual = conn.scalar(
            sa.text("""SELECT sum(l.ordered_quantity) FROM sales_order_lines l
            JOIN sales_orders o ON (o.org_id,o.id)=(l.org_id,l.order_id)
            WHERE o.confirmed_at IS NOT NULL""")
        )
        assert gross == actual
        # Completed and subsequently cancelled confirmed orders remain gross demand.
        assert conn.scalar(sa.text("SELECT count(*) FROM sales_orders WHERE status='COMPLETED'"))
    with databases["warehouse"].connect() as conn:
        assert not conn.scalar(
            sa.text("""SELECT count(*) FROM v_stock_availability
            WHERE available_quantity <> on_hand_quantity-reserved_quantity""")
        )


def test_inbound_constraints_and_partial_posted_receipt_view(databases):
    engine = databases["warehouse"]
    plans = table("warehouse", "inbound_plans")
    line = clone_row(engine, table("warehouse", "goods_receipt_lines"))
    with engine.connect() as conn:
        header = (
            conn.execute(
                sa.select(table("warehouse", "goods_receipts")).where(
                    table("warehouse", "goods_receipts").c.id == line["receipt_id"]
                )
            )
            .mappings()
            .one()
        )
    plan = dict(
        id=uuid4(),
        org_id=line["org_id"],
        warehouse_id=header["warehouse_id"],
        product_id=line["product_id"],
        planned_quantity=line["accepted_quantity"] + 10,
        expected_at=header["received_at"],
        status="PARTIALLY_RECEIVED",
    )
    reject(engine, plans.insert().values(**{**plan, "planned_quantity": 0}), "23514")
    reject(engine, plans.insert().values(**{**plan, "org_id": uuid4()}), "23503")
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(plans.insert(), plan)
            conn.execute(
                table("warehouse", "goods_receipt_lines")
                .update()
                .where(table("warehouse", "goods_receipt_lines").c.id != line["id"])
                .where(table("warehouse", "goods_receipt_lines").c.receipt_id == line["receipt_id"])
                .where(table("warehouse", "goods_receipt_lines").c.product_id == line["product_id"])
                .values(inbound_plan_id=plan["id"])
            )
            remaining = conn.scalar(
                sa.text(
                    "SELECT remaining_quantity FROM v_inbound_schedule WHERE inbound_plan_id=:id"
                ),
                plan,
            )
            assert remaining == 10
            conn.execute(
                table("warehouse", "goods_receipts")
                .update()
                .where(table("warehouse", "goods_receipts").c.id == line["receipt_id"])
                .values(status="DRAFT", posted_at=None)
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT remaining_quantity FROM v_inbound_schedule "
                        "WHERE inbound_plan_id=:id"
                    ),
                    plan,
                )
                == plan["planned_quantity"]
            )
        finally:
            tx.rollback()


def test_capacity_counts_handed_over_and_releases_cancelled(databases):
    engine = databases["logistics"]
    target = table("logistics", "shipments")
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            sh = (
                conn.execute(sa.select(target).where(target.c.status == "HANDED_OVER"))
                .mappings()
                .first()
            )
            calendar = metadata_for("logistics").tables["public.handoff_capacity_calendar"]
            day = conn.scalar(
                sa.text("SELECT (:ts AT TIME ZONE 'Asia/Ho_Chi_Minh')::date"),
                {"ts": sh["handed_over_at"]},
            )
            values = dict(
                id=uuid4(),
                org_id=sh["org_id"],
                carrier_code=sh["carrier_code_snapshot"],
                capacity_date=day,
                max_dispatches=1,
                status="ACTIVE",
            )
            conn.execute(calendar.insert(), values)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT used_dispatches FROM v_handoff_capacity "
                        "WHERE carrier_code=:carrier_code AND capacity_date=:capacity_date"
                    ),
                    values,
                )
                == 1
            )
            conn.execute(
                target.update()
                .where(target.c.id == sh["id"])
                .values(status="CANCELLED", cancelled_at=sh["handed_over_at"], handed_over_at=None)
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT used_dispatches FROM v_handoff_capacity "
                        "WHERE carrier_code=:carrier_code AND capacity_date=:capacity_date"
                    ),
                    values,
                )
                == 0
            )
        finally:
            tx.rollback()


def test_calendar_negative_capacity_and_duplicate_slot_rejected(databases):
    import datetime

    calendar = table("logistics", "handoff_capacity_calendar")
    values = dict(
        id=uuid4(),
        org_id=uuid4(),
        carrier_code="TEST",
        capacity_date=datetime.date(2026, 1, 1),
        max_dispatches=-1,
        status="ACTIVE",
    )
    reject(databases["logistics"], calendar.insert().values(**values), "23514")
    from sqlalchemy.exc import IntegrityError

    with databases["logistics"].connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(calendar.insert().values(**{**values, "max_dispatches": 0}))
            with pytest.raises(IntegrityError) as error, conn.begin_nested():
                conn.execute(
                    calendar.insert().values(**{**values, "id": uuid4(), "max_dispatches": 1})
                )
            assert error.value.orig.sqlstate == "23505"
        finally:
            tx.rollback()


def test_live_view_grain_proof_rejects_duplicates_and_nulls(databases):
    from app.core.data_sources.discovery import DiscoveryError, DiscoverySource, inspect_schema
    from app.core.hasura_provisioner.schemas import TableRef

    source = DiscoverySource(
        key="proof-test",
        host="127.0.0.1",
        port=5432,
        database="disposable",
        username="reader",
        ssl_mode="require",
        secret_ref="test",
        objects=[TableRef(schema="public", name="proof_test")],
    )
    with databases["sales"].connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(sa.text("CREATE VIEW proof_test AS SELECT NULL::text AS key"))
            with pytest.raises(DiscoveryError):
                inspect_schema(conn, source, view_keys={"public.proof_test": ("key",)})
            conn.execute(
                sa.text(
                    "CREATE OR REPLACE VIEW proof_test AS SELECT 'x'::text AS key "
                    "UNION ALL SELECT 'x'::text"
                )
            )
            with pytest.raises(DiscoveryError):
                inspect_schema(conn, source, view_keys={"public.proof_test": ("key",)})
            conn.execute(sa.text("CREATE OR REPLACE VIEW proof_test AS SELECT 'x'::text AS key"))
            view = inspect_schema(conn, source, view_keys={"public.proof_test": ("key",)}).objects[
                0
            ]
            assert not view.columns[0].nullable
            assert view.unique_constraints == (("key",),)
        finally:
            tx.rollback()
