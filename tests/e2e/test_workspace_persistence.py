# SPDX-License-Identifier: GPL-3.0-or-later
"""Real disposable PostgreSQL; this suite never reads a deployed .env."""

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from scripts.decision_test_environment import validate_test_url

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def migrated_workspace():
    value = os.environ.get("FLOWBRIDGE_WORKSPACE_TEST_DATABASE_URL")
    if not value or not os.environ.get("FLOWBRIDGE_DECISION_TEST_RUN"):
        pytest.skip("Use scripts/test_decision_workspace_local.ps1 for disposable PostgreSQL")
    validate_test_url(value)
    assert os.environ["FLOWBRIDGE_WORKSPACE_DATABASE_URL"] == value
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "services/data-workspace-api/migrations"))
    command.upgrade(cfg, "head")
    # Replaying the upgrade must be a no-op.
    command.upgrade(cfg, "head")
    spec = importlib.util.spec_from_file_location(
        "workspace_persistence_test",
        ROOT / "services/data-workspace-api/app/__init__.py",
        submodule_search_locations=[str(ROOT / "services/data-workspace-api/app")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    from workspace_persistence_test.repository import PostgresRepository

    return value, PostgresRepository


@pytest.mark.asyncio
async def test_real_migration_restart_and_private_results(migrated_workspace):
    url, repository_type = migrated_workspace
    ctx = SimpleNamespace(org_id=uuid4(), owner_id=uuid4())
    payload = {"name": "Kế hoạch bàn giao", "amount": 30, "unknown": None}
    first = repository_type(url)
    saved = await first.save("simulation", ctx, payload)
    await first.close()
    reopened = repository_type(url)
    try:
        assert await reopened.get("simulation", saved["id"], ctx) == saved
        assert saved in await reopened.list("simulation", ctx)
        from fastapi import HTTPException

        for stranger in [
            SimpleNamespace(org_id=ctx.org_id, owner_id=uuid4()),
            SimpleNamespace(org_id=uuid4(), owner_id=ctx.owner_id),
        ]:
            with pytest.raises(HTTPException) as error:
                await reopened.get("simulation", saved["id"], stranger)
            assert error.value.status_code == 404
    finally:
        await reopened.close()
