from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from scripts.database.migration import alembic_config
from scripts.database.registry import DATABASES, metadata_for
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


def reject(engine, statement, expected_code):
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            with pytest.raises(IntegrityError) as error:
                conn.execute(statement)
            assert error.value.orig.sqlstate == expected_code
        finally:
            tx.rollback()


def table(database, name):
    return metadata_for(database).tables[f"public.{name}"]


def clone_row(engine, target):
    with engine.connect() as conn:
        row = dict(conn.execute(select(target)).mappings().first())
    row["id"] = uuid4()
    return row


def test_all_heads_match_models_and_seed_replays(databases):
    from scripts.database.seed import seed_databases
    from scripts.database.verify import verify_demo

    assert seed_databases(DATABASES) == dict.fromkeys(DATABASES, 0)
    for name in DATABASES:
        command.upgrade(alembic_config(name), "head")
        command.check(alembic_config(name))
        actual = set(inspect(databases[name]).get_table_names(schema="public")) - {
            "alembic_version"
        }
        assert actual == {t.name for t in metadata_for(name).tables.values()}
    assert verify_demo()


def test_cross_organization_fk_rejected(databases):
    target = table("sales", "sales_orders")
    row = clone_row(databases["sales"], target)
    row.update(org_id=uuid4(), order_number="TEST-CROSS-ORG")
    reject(databases["sales"], target.insert().values(**row), "23503")


def test_negative_stock_or_over_reservation_rejected(databases):
    target = table("warehouse", "inventory_balances")
    reject(
        databases["warehouse"],
        target.update().values(reserved_quantity=target.c.on_hand_quantity + 1),
        "23514",
    )


def test_zero_quantity_order_line_rejected(databases):
    target = table("sales", "sales_order_lines")
    reject(databases["sales"], target.update().values(ordered_quantity=Decimal(0)), "23514")


def test_dispatch_cannot_use_another_reservation_line(databases):
    target = table("warehouse", "dispatch_lines")
    rows = table("warehouse", "reservation_lines")
    with databases["warehouse"].connect() as conn:
        current = conn.execute(select(target)).mappings().first()
        other = (
            conn.execute(select(rows).where(rows.c.reservation_id != current["reservation_id"]))
            .mappings()
            .first()
        )
    assert other is not None
    reject(
        databases["warehouse"],
        target.update().where(target.c.id == current["id"]).values(reservation_line_id=other["id"]),
        "23503",
    )


def test_stock_movement_cannot_claim_both_origins(databases):
    target = table("warehouse", "stock_movements")
    row = clone_row(databases["warehouse"], target)
    row.update(goods_receipt_line_id=uuid4(), dispatch_line_id=uuid4())
    # CHECK constraints run before the invented FK origins could be accepted.
    reject(databases["warehouse"], target.insert().values(**row), "23514")


def test_completed_sales_requires_timestamp(databases):
    target = table("sales", "sales_orders")
    reject(
        databases["sales"],
        target.update().values(status="COMPLETED", completed_at=None),
        "23514",
    )


def test_logistics_cannot_mark_handoff_without_evidence(databases):
    target = table("logistics", "shipments")
    reject(
        databases["logistics"],
        target.update().values(
            status="HANDED_OVER", handed_over_at=None, handed_over_event_ref=None
        ),
        "23514",
    )


def test_seed_refuses_to_overwrite_modified_fixture(databases):
    from scripts.database.seed import _validate_existing, demo_rows

    target = table("sales", "customers")
    with databases["sales"].connect() as conn:
        tx = conn.begin()
        conn.execute(target.update().values(name="User edited this record"))
        try:
            with pytest.raises(RuntimeError, match="does not overwrite"):
                _validate_existing(conn, metadata_for("sales"), demo_rows()["sales"])
        finally:
            tx.rollback()


def test_migration_roundtrip_and_core_priority_boundaries(databases):
    import json
    from io import StringIO

    from scripts.database.config import database_url
    from scripts.database.registry import REPOSITORY_ROOT
    from scripts.database.seed import seed_databases

    legacy_priorities = json.loads(
        (REPOSITORY_ROOT / "tests/fixtures/database/handoff-fingerprints.json").read_text()
    )["legacy_core_priorities"]

    # Test harness creates these database names, never the user's .env targets.
    for name in DATABASES:
        assert database_url(name).database.startswith("dxos_it_")
        config = alembic_config(name)
        config.attributes["allow_core_data_reset"] = name == "core"
        command.downgrade(config, "base")
        assert set(inspect(databases[name]).get_table_names(schema="public")) == {"alembic_version"}
    for revision, priorities in (
        ("core_0001", {"P0"}),
        ("core_0002", {"P0", "P1"}),
        ("core_0003", {"P0", "P1", "P2"}),
    ):
        command.upgrade(alembic_config("core"), revision)
        expected = {name for name, priority in legacy_priorities.items() if priority in priorities}
        assert set(inspect(databases["core"]).get_table_names(schema="public")) == expected | {
            "alembic_version"
        }
    for name in DATABASES:
        # Execute offline SQL too, with a deliberately different search path.
        config = alembic_config(name)
        if name != "core":
            output = StringIO()
            config.output_buffer = output
            command.upgrade(config, "head", sql=True)
            sql = output.getvalue()
            assert "CREATE TABLE public.alembic_version" in sql
            with databases[name].connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.exec_driver_sql("SET search_path TO pg_catalog")
                # Offline base->head targets a truly empty database. Alembic's
                # online downgrade deliberately retains its empty version table.
                conn.exec_driver_sql("DROP TABLE public.alembic_version")
                conn.exec_driver_sql(sql, execution_options={"no_parameters": True})
        command.upgrade(alembic_config(name), "head")
    seed_databases(DATABASES)
