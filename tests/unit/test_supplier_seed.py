# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Offline validation of the supplied baseline and causal synthetic data.

import json
import runpy
from datetime import datetime
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from flowbridge.models.base import Base

ROOT = Path(__file__).resolve().parents[2]


def test_registered_models_cover_the_agreed_schema():
    # Baseline remains 27 tables; retry state and the chat projection are separate migrations.
    extensions = {"api_idempotency", "chat_sessions", "chat_turns"}
    tables = [table for name, table in Base.metadata.tables.items() if name not in extensions]
    assert len(tables) == 27
    assert sum(len(table.columns) for table in tables) == 293
    assert (
        sum(
            isinstance(constraint, ForeignKeyConstraint)
            for table in tables
            for constraint in table.constraints
        )
        == 56
    )
    assert (
        sum(
            isinstance(constraint, CheckConstraint)
            for table in tables
            for constraint in table.constraints
        )
        == 83
    )
    for table in tables:
        assert table.primary_key.columns
        assert table.comment
        for column in table.columns:
            assert column.comment


def test_migration_sql_matches_supplied_installer():
    source = runpy.run_path(str(ROOT / "scripts/supplier_demo_source.py"))
    for constant, filename in (
        ("SCHEMA_SQL", "schema_0001.sql"),
        ("HELPER_SQL", "helper_0001.sql"),
        ("VERIFY_SQL", "verify_0001.sql"),
    ):
        assert (ROOT / "alembic/sql" / filename).read_text(encoding="utf-8") == source[constant]


def test_demo_labels_preserve_knowledge_time_and_grouped_split(tmp_path):
    source = runpy.run_path(str(ROOT / "scripts/supplier_demo_source.py"))
    asof = datetime(2026, 10, 15, 18, tzinfo=source["TZ"])
    demo = source["Demo"](50, 20261015, asof).simulate()
    report = demo.exports(tmp_path)
    assert all(report["row_counts"].values())
    assert report["row_counts"]["purchase_requests"] == 50
    assert report["model_has_run"] is False
    assert report["context_rows"] > 0
    assert report["train_rows"] > 0
    assert report["test_rows"] > 0
    persisted = json.loads((tmp_path / "simulation_report.json").read_text(encoding="utf-8"))
    assert persisted["leakage_audit"].startswith("passed")
    # exports() also asserts disjoint PO groups and labels available before train cutoff.
    for sample in demo.samples:
        assert sample["at"] <= asof
        order = sample["order"]
        if demo.completed(order, asof):
            assert order["final_known"] <= asof
