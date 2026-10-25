# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from tests.e2e.database.test_postgresql import reject, table

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


def test_warehouse_policy_is_unknown_until_all_confirmation_fields_are_present(databases):
    engine = databases["warehouse"]
    warehouses = table("warehouse", "warehouses")
    with engine.connect() as conn:
        row = conn.execute(sa.select(warehouses)).mappings().first()
        policy = (
            conn.execute(
                sa.text("SELECT * FROM v_handoff_preparation_policy WHERE warehouse_id=:id"), row
            )
            .mappings()
            .one()
        )
        assert policy["preparation_minutes"] is None
        assert policy["preparation_policy_revision"] == 0
    base = warehouses.update().where(warehouses.c.id == row["id"])
    reject(engine, base.values(preparation_minutes=120), "23514")
    reject(engine, base.values(preparation_policy_revision=1), "23514")
    complete = {
        "preparation_policy_revision": 1,
        "preparation_minutes": 120,
        "preparation_confirmed_at": datetime.now(UTC),
        "preparation_confirmed_by": "warehouse-policy-owner",
    }
    reject(engine, base.values(**{**complete, "preparation_minutes": -1}), "23514")
    reject(engine, base.values(**{**complete, "preparation_confirmed_by": None}), "23514")
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(base.values(**complete))
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT preparation_minutes FROM v_handoff_preparation_policy "
                        "WHERE warehouse_id=:id"
                    ),
                    row,
                )
                == 120
            )
        finally:
            transaction.rollback()
