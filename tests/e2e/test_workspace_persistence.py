# SPDX-License-Identifier: GPL-3.0-or-later
"""Real disposable PostgreSQL; this suite never reads a deployed .env."""

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from scripts.decision_test_environment import validate_test_url

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def migrated_workspace():
    value = os.environ.get("FLOWBRIDGE_WORKSPACE_TEST_DATABASE_URL")
    if not value or not os.environ.get("FLOWBRIDGE_DECISION_TEST_RUN"):
        pytest.skip("Use scripts/test_decision_workspace_local.ps1 for disposable PostgreSQL")
    validate_test_url(value)
    assert os.environ["FLOWBRIDGE_WORKSPACE_DATABASE_URL"] == value
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "services/data-workspace-api/migrations"))
    # Prove additive upgrade preserves an existing private result, rather than
    # testing only creation of a fresh latest schema.
    command.upgrade(cfg, "workspace_0001")
    import sqlalchemy as sa

    legacy_id, org, owner = uuid4(), uuid4(), uuid4()
    engine = sa.create_engine(value)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO data_workspace.simulation_runs(id,org_id,owner_id,payload) "
                "VALUES (:id,:org,:owner,CAST(:payload AS jsonb))"
            ),
            {"id": legacy_id, "org": org, "owner": owner, "payload": '{"legacy":true}'},
        )
    command.upgrade(cfg, "head")
    # Replaying the upgrade must be a no-op.
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert conn.scalar(
            sa.text("SELECT payload FROM data_workspace.simulation_runs WHERE id=:id"),
            {"id": legacy_id},
        ) == {"legacy": True}
    engine.dispose()
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


@pytest.mark.asyncio
async def test_decision_transactions_revision_history_and_scope(migrated_workspace):
    from fastapi import HTTPException
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError
    from workspace_persistence_test.decision_models import cases, revisions
    from workspace_persistence_test.decision_repository import DecisionRepository

    url, repository_type = migrated_workspace
    result_repo = repository_type(url)
    repo = DecisionRepository(result_repo.engine)
    org, warehouse, order, owner, manager = [uuid4() for _ in range(5)]
    ctx = SimpleNamespace(org_id=org, owner_id=owner)
    review = SimpleNamespace(org_id=org, owner_id=manager)
    saved = await result_repo.save("simulation", ctx, {"snapshot": 1})
    run_id = UUID(saved["id"])
    kwargs = dict(
        order_id=order,
        warehouse_id=warehouse,
        simulation_id=run_id,
        title="Bổ sung hàng cho đơn cần bàn giao",
        key="create-1",
        roster=[
            {"principal_id": str(owner), "role": "coordinator"},
            {"principal_id": str(manager), "role": "manager"},
        ],
        payload={"snapshot": "original", "requires_revalidation": True},
    )
    try:
        # Simultaneous retries must have one effect and one business key.
        first, retry = await asyncio.gather(repo.create(ctx, **kwargs), repo.create(ctx, **kwargs))
        identifier = UUID(first["id"])
        assert first == retry
        async with repo.engine.connect() as conn:
            assert (
                await conn.scalar(
                    select(func.count()).select_from(cases).where(cases.c.id == identifier)
                )
                == 1
            )
        assert (await repo.get(review, identifier))["revision"]["payload"] == kwargs["payload"]
        # Sharing a dossier must not make the private simulation visible.
        with pytest.raises(HTTPException) as private:
            await result_repo.get("simulation", run_id, review)
        assert private.value.status_code == 404
        for outsider in (
            SimpleNamespace(org_id=org, owner_id=uuid4()),
            SimpleNamespace(org_id=uuid4(), owner_id=owner),
        ):
            with pytest.raises(HTTPException) as denied:
                await repo.get(outsider, identifier)
            assert denied.value.status_code == 404
        for conflict in ({"title": "Different command"}, {"key": "another-active-case"}):
            with pytest.raises(HTTPException) as duplicate:
                await repo.create(ctx, **{**kwargs, **conflict})
            assert duplicate.value.status_code == 409
        with pytest.raises(HTTPException) as not_owner:
            await repo.revise(
                review,
                identifier,
                simulation_id=run_id,
                expected_version=1,
                payload={},
                key="review-revise",
            )
        assert not_owner.value.status_code == 403

        async def revise(key):
            try:
                return await repo.revise(
                    ctx,
                    identifier,
                    simulation_id=run_id,
                    expected_version=1,
                    payload={"snapshot": key},
                    key=key,
                )
            except HTTPException as error:
                return error.status_code

        concurrent = await asyncio.gather(revise("edit-a"), revise("edit-b"))
        assert sum(isinstance(item, dict) for item in concurrent) == 1
        assert 409 in concurrent
        winner = next(item for item in concurrent if isinstance(item, dict))
        assert winner["current_revision"] == winner["version"] == 2
        replay = await repo.revise(
            ctx,
            identifier,
            simulation_id=run_id,
            expected_version=1,
            payload={"snapshot": winner["revision"]["command_key"]},
            key=winner["revision"]["command_key"],
        )
        assert replay["revision"]["id"] == winner["revision"]["id"]
        history = await repo.history(review, identifier)
        assert [r["number"] for r in history] == [2, 1]
        assert history[1]["payload"] == kwargs["payload"]
        assert [e["event_type"] for e in await repo.timeline(review, identifier)] == [
            "CASE_CREATED",
            "REVISION_CREATED",
        ]
        # The cross-scope simulation FK is enforced by PostgreSQL, not merely Python.
        foreign = await result_repo.save(
            "simulation", SimpleNamespace(org_id=uuid4(), owner_id=owner), {}
        )
        with pytest.raises(IntegrityError):
            async with repo.engine.begin() as conn:
                await conn.execute(
                    revisions.insert().values(
                        id=uuid4(),
                        org_id=org,
                        case_id=identifier,
                        number=3,
                        simulation_run_id=UUID(foreign["id"]),
                        created_by=owner,
                        command_key="bad-scope",
                        command_hash="a" * 64,
                        payload={},
                    )
                )
        await result_repo.close()
        reopened = repository_type(url)
        try:
            restored = await DecisionRepository(reopened.engine).get(review, identifier)
            assert restored["revision"] == winner["revision"]
        finally:
            await reopened.close()
    finally:
        await result_repo.close()


@pytest.mark.asyncio
async def test_decision_http_sharing_does_not_bypass_current_source_grants(migrated_workspace):
    """Real Workspace HTTP+PostgreSQL; Core boundary is deliberately mocked here."""
    import httpx
    from fastapi import HTTPException
    from workspace_persistence_test.connector import Context
    from workspace_persistence_test.runtime import PREFIX, create_app

    url, result_type = migrated_workspace
    results = result_type(url)
    org, warehouse, order = uuid4(), uuid4(), uuid4()
    actors = {name: uuid4() for name in ("sales", "warehouse", "logistics", "manager", "stranger")}
    roles = {
        "sales": "coordinator",
        "warehouse": "warehouse",
        "logistics": "logistics",
        "manager": "manager",
    }
    bindings = {
        str(actors[name]): {"org_id": str(org), "warehouse_ids": [str(warehouse)], "roles": [role]}
        for name, role in roles.items()
    }
    denied = set()

    class GovernedBoundary:
        async def context(self, token):
            if token not in actors:
                raise HTTPException(401)
            return Context(token, org, actors[token], False)

        async def check(self, ctx, ref, columns):
            if (ctx.token, str(ref.data_product_id)) in denied:
                raise HTTPException(403)

    source = {
        "ref": {"data_product_id": str(uuid4()), "contract_version_id": str(uuid4())},
        "columns": ["org_id", "sku"],
    }
    ctx = Context("sales", org, actors["sales"], False)
    forecast = await results.save(
        "forecast",
        ctx,
        {
            "sources": [source],
            "points": [{"item_id": "COFFEE", "p50": 10}, {"item_id": "TEA", "p50": 20}],
        },
    )
    payload = {
        "sources": [source],
        "decision": {"verdict": "covered"},
        "evidence": {
            "metric_semantics_version": "physical_supply_deficit_v1",
            "assumptions": {
                "warehouse_id": str(warehouse),
                "sku": "COFFEE",
                "forecast_run_id": forecast["id"],
            },
        },
        "baseline": {
            "orders": [
                {"order_id": str(order), "lines": [{"sku": "COFFEE"}]},
                {"order_id": str(uuid4()), "lines": [{"sku": "TEA"}]},
            ]
        },
        "proposed": {"orders": []},
    }
    simulation = await results.save("simulation", ctx, payload)
    app = create_app(GovernedBoundary(), results, decision_bindings=bindings)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://workspace"
        ) as client:

            def auth(name):
                return {"Authorization": "Bearer " + name}

            body = {
                "simulation_run_id": simulation["id"],
                "order_id": str(order),
                "title": "Bổ sung hàng trước hạn",
            }
            response = await client.post(
                PREFIX + "/decisions",
                json=body,
                headers={**auth("sales"), "Idempotency-Key": "http-create"},
            )
            assert response.status_code == 201, response.text
            case = response.json()
            route = PREFIX + "/decisions/" + case["id"]
            assert (
                case["status"] == "DRAFT" and case["revision"]["payload"]["requires_revalidation"]
            )
            assert len(case["revision"]["payload"]["scenario"]["baseline"]["orders"]) == 1
            assert len(case["revision"]["payload"]["forecast"]["points"]) == 1
            assert (await client.get(route, headers=auth("manager"))).status_code == 200
            assert (
                await client.get(PREFIX + "/scenarios/" + simulation["id"], headers=auth("manager"))
            ).status_code == 404
            assert (await client.get(route, headers=auth("stranger"))).status_code == 404
            assert (
                await client.post(
                    route + "/revisions",
                    headers={**auth("manager"), "Idempotency-Key": "forged"},
                    json={"simulation_run_id": simulation["id"], "expected_version": 1},
                )
            ).status_code == 403
            # Bindings are necessary but never override a revoked Core source grant.
            denied.add(("manager", source["ref"]["data_product_id"]))
            for suffix in ("", "/revisions", "/timeline"):
                assert (
                    await client.get(route + suffix, headers=auth("manager"))
                ).status_code == 403
            listing = (await client.get(PREFIX + "/decisions", headers=auth("manager"))).json()
            assert listing["items"] == [] and listing["warnings"]
            assert (
                await client.post(PREFIX + "/decisions", json=body, headers=auth("sales"))
            ).status_code == 422
    finally:
        await results.close()
