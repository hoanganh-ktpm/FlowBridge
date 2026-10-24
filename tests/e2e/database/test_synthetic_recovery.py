"""Exercise partial cross-database commit/recovery on disposable local databases."""

import json
import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from scripts.database import synthetic
from scripts.database.migration import alembic_config
from scripts.database.registry import metadata_for
from scripts.database.simulation import SimulationConfig
from sqlalchemy.engine import make_url

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


@pytest.fixture
def simulation_databases(monkeypatch, tmp_path):
    value = os.environ.get("DXOS_TEST_ADMIN_URL")
    if not value:
        pytest.skip("Explicit disposable PostgreSQL admin URL required")
    admin_url = make_url(value)
    prefix = "dxos_it_sim_" + uuid4().hex[:10] + "_"
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    engines, created = {}, []
    monkeypatch.setenv("DXOS_LAYOUT", "databases")
    monkeypatch.setattr(synthetic, "LEDGER", tmp_path / "run.json")
    try:
        with admin.connect() as conn:
            for domain in ("sales", "warehouse", "logistics"):
                name = prefix + domain
                conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
                created.append(name)
                url = admin_url.set(database=name)
                monkeypatch.setenv(
                    f"DXOS_{domain.upper()}_URL", url.render_as_string(hide_password=False)
                )
                engines[domain] = sa.create_engine(url)

        # The production guard deliberately allows only exact demo names. In this
        # test use a stricter freshly-created target set, with no .env fallback.
        def disposable_guard():
            from scripts.database.config import database_url

            assert {database_url(d).database for d in engines} == set(created)
            assert all(name.startswith(prefix) for name in created)

        monkeypatch.setattr(synthetic, "assert_targets", disposable_guard)
        for domain in engines:
            command.upgrade(alembic_config(domain), "head")
        yield engines
    finally:
        for engine in engines.values():
            engine.dispose()
        with admin.connect() as conn:
            for name in reversed(created):
                assert name.startswith(prefix)
                conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def test_interrupted_commit_resumes_without_duplicates_and_refuses_unowned_rows(
    simulation_databases, monkeypatch
):
    config = SimulationConfig(days=60, scale=0.15)
    original = synthetic.insert_missing_rows
    calls = 0

    def interrupted(conn, metadata, rows):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected failure after Sales committed")
        return original(conn, metadata, rows)

    monkeypatch.setattr(synthetic, "insert_missing_rows", interrupted)
    with pytest.raises(RuntimeError, match="injected failure"):
        synthetic.generate(config)
    assert json.loads(synthetic.LEDGER.read_text())["state"] == "IN_PROGRESS"
    with simulation_databases["sales"].connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM sales_orders")) > 0
    with simulation_databases["warehouse"].connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM products")) == 0
    monkeypatch.setattr(synthetic, "insert_missing_rows", original)
    assert synthetic.generate(config)["valid"]
    assert synthetic.generate(config)["inserted"] == dict.fromkeys(simulation_databases, 0)
    ledger = json.loads(synthetic.LEDGER.read_text())
    assert ledger["state"] == "COMPLETE"
    # An extra real/unknown record blocks reset before any database is mutated.
    engine = simulation_databases["sales"]
    customers = metadata_for("sales").tables["public.customers"]
    with engine.begin() as conn:
        conn.execute(
            customers.insert().values(
                id=uuid4(),
                org_id=uuid4(),
                customer_code="UNKNOWN",
                name="User data",
                created_by_subject="user",
            )
        )
    with pytest.raises(ValueError, match="unowned row"):
        synthetic.generate(config, reset=True)
    assert json.loads(synthetic.LEDGER.read_text()) == ledger
