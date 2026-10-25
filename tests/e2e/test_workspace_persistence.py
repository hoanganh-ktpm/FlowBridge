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
        current_revision = UUID(winner["revision"]["id"])
        await repo.begin_submission(ctx, identifier, 2, current_revision, "submit-current")
        await repo.workflow_projection(
            ctx, identifier, current_revision, "scoped-engine-instance", "APPROVED"
        )
        proof = {
            "kind": "DECISION_COMPLETED",
            "instance_id": "scoped-engine-instance",
            "revision_id": str(current_revision),
            "completed_at": "2026-10-25T09:00:00+07:00",
            "source_event_ids": [str(uuid4())],
            "proof_sha256": "a" * 64,
        }
        with pytest.raises(HTTPException) as wrong_instance:
            await repo.complete_handoff(
                ctx, identifier, current_revision, {**proof, "instance_id": "foreign-instance"}
            )
        assert wrong_instance.value.status_code == 409
        closed = await repo.complete_handoff(ctx, identifier, current_revision, proof)
        assert closed["status"] == "COMPLETED" and closed["active"] is False
        assert await repo.complete_handoff(ctx, identifier, current_revision, proof) == closed
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
            assert restored["status"] == "COMPLETED"
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


@pytest.mark.asyncio
async def test_workspace_core_flowable_bridge_restart_retry_and_stale_approval(migrated_workspace):
    """Real Workspace HTTP/PG + Core workflow logic + Flowable; source/IAM boundaries mocked."""
    from copy import deepcopy
    from datetime import UTC, datetime, timedelta

    import httpx
    import sqlalchemy as sa
    from fastapi import HTTPException

    base = os.environ.get("FLOWBRIDGE_DECISION_FLOWABLE_TEST_URL")
    if not base:
        pytest.skip("Run the isolated decision_workflow_test_environment profile")
    # Share the independent service package with existing deterministic business fixtures.
    sys.modules["workspace_app"] = sys.modules["workspace_persistence_test"]
    from tests.core.runtime.fakes import MemoryStore
    from tests.core.runtime.test_decision_workflow import fixture as core_fixture
    from tests.domain.data_workspace.test_decision_workspace import PlanningBoundary
    from workspace_persistence_test.connector import Context
    from workspace_persistence_test.runtime import PREFIX, create_app

    from app.platform.workflow.decision_schemas import DecisionStart, DecisionTaskCommand
    from app.platform.workflow.flowable import FlowableClient

    store = MemoryStore()
    runs, executor, original, actors = await core_fixture(SimpleNamespace(store=store))
    org, wh, order = original.org_id, original.warehouse_id, str(original.order_id)

    class Sources(PlanningBoundary):
        async def context(self, token):
            if token not in actors or token == "executor":
                raise HTTPException(401)
            return Context(token, org, actors[token], False)

        async def rows(self, ctx, ref, columns=None, where=None):
            assert ctx.org_id == org
            product, values = await super().rows(self.ctx, ref, columns, where)
            # Model Core's column selection and source filters, not cached full rows.
            if where:
                for field, predicate in where.items():
                    if "_eq" in predicate:
                        values = [row for row in values if row[field] == predicate["_eq"]]
                    elif "_in" in predicate:
                        values = [row for row in values if row[field] in predicate["_in"]]
            return product, [
                {key: row[key] for key in set(columns or row) | {"org_id"}} for row in values
            ]

        async def catalog(self, ctx):
            return list(self.products.values())

    boundary = Sources()
    old_wh, old_org = boundary.wh, str(boundary.ctx.org_id)
    boundary.wh = str(wh)
    boundary.ctx = Context("coordinator", org, actors["coordinator"], False)
    for rows in boundary.facts.values():
        for row in rows:
            if row.get("warehouse_id") == old_wh:
                row["warehouse_id"] = str(wh)
            if row.get("org_id") == old_org:
                row["org_id"] = str(org)
            if row.get("order_id") == "order-1":
                row["order_id"] = order
    tomorrow = boundary.today + timedelta(days=1)
    boundary.facts[boundary.refs["schedule"]["data_product_id"]][0]["scheduled_handoff_at"] = (
        f"{tomorrow}T09:00:00+07:00"
    )
    cap = boundary.facts[boundary.refs["capacity"]["data_product_id"]][0]
    cap.update(capacity_date=str(tomorrow), used_dispatches=1)
    prep = {"data_product_id": str(uuid4()), "contract_version_id": str(uuid4())}
    preparation = {
        "org_id": str(org),
        "warehouse_id": str(wh),
        "preparation_minutes": 120,
        "preparation_policy_revision": 1,
        "preparation_confirmed_at": datetime.now(UTC).isoformat(),
        "preparation_confirmed_by": "Phụ trách kho",
    }
    boundary.refs["preparation"] = prep
    boundary.products[prep["data_product_id"]] = {
        "id": prep["data_product_id"],
        "contract_version_id": prep["contract_version_id"],
        "name": "warehouse.handoff_preparation_policy",
        "business_key": ["org_id", "warehouse_id"],
        "fields": [{"name": c} for c in preparation],
    }
    boundary.facts[prep["data_product_id"]] = [preparation]
    bindings = {
        str(principal): {"org_id": str(org), "warehouse_ids": [str(wh)], "roles": [role]}
        for role, principal in actors.items()
        if role != "executor"
    }
    results = migrated_workspace[1](migrated_workspace[0])
    calls = []
    async with httpx.AsyncClient(timeout=30) as http:
        engine = FlowableClient(
            http,
            base,
            os.environ["FLOWBRIDGE_DECISION_FLOWABLE_TEST_USER"],
            os.environ["FLOWBRIDGE_DECISION_FLOWABLE_TEST_PASSWORD"],
            30,
        )
        await engine.deploy(
            "decision-bridge",
            (ROOT / "workflows/supply_handover_exception.bpmn20.xml").read_text(encoding="utf-8"),
        )
        runs.engine = engine

        class CorePort:
            lost = True

            async def request(self, path, body=None, key=None, params=None):
                from app.platform.http.errors import CoreError, ErrorCode

                try:
                    return await self.execute(path, body, key, params)
                except CoreError as error:
                    status = (
                        403
                        if error.code == ErrorCode.FORBIDDEN
                        else 409
                        if error.code
                        in {
                            ErrorCode.INVALID_STATE,
                            ErrorCode.VERSION_CONFLICT,
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                        }
                        else 503
                    )
                    raise HTTPException(
                        status, "Core workflow refused or could not confirm this command"
                    ) from None

            async def execute(self, path, body=None, key=None, params=None):
                calls.append((path, deepcopy(body)))
                if path == "/decision-runs/commands/start":
                    value = await runs.start(executor, DecisionStart.model_validate(body), "bridge")
                    if self.lost:
                        self.lost = False
                        raise HTTPException(503, "Simulated lost start response")
                    return value
                if path == "/decision-runs/commands/reconcile-start":
                    from app.platform.workflow.decision_schemas import DecisionReconcileStart

                    return await runs.reconcile_start(
                        executor, DecisionReconcileStart.model_validate(body)
                    )
                fields = path.split("/")
                if body is None:
                    return await runs.state(executor, fields[2], UUID(params["principal_id"]))
                return await runs.complete(
                    executor, fields[2], fields[4], DecisionTaskCommand.model_validate(body), key
                )

        application = create_app(
            boundary, results, decision_bindings=bindings, decision_workflow=CorePort()
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(application), base_url="http://workspace"
            ) as api:

                def headers(role="coordinator", key=None):
                    return {
                        "Authorization": "Bearer " + role,
                        **({"Idempotency-Key": key} if key else {}),
                    }

                plan = await api.post(
                    PREFIX + "/planning",
                    headers=headers(),
                    json={**boundary.refs, "warehouse_id": str(wh), "sku": "A", "horizon": 7},
                )
                assert plan.status_code == 200, plan.text
                simulation_body = {
                    "forecast_run_id": plan.json()["forecast"]["id"],
                    "sku": "A",
                    "warehouse_id": str(wh),
                    "arrival_date": str(boundary.today),
                    "additional_quantity": 100,
                    "save": True,
                    **{
                        key: boundary.refs[key]
                        for key in ("stock", "inbound", "open_orders", "commitments", "preparation")
                    },
                }
                scenario = await api.post(
                    PREFIX + "/scenarios", headers=headers(), json=simulation_body
                )
                assert scenario.status_code == 200, scenario.text
                response = await api.post(
                    PREFIX + "/decisions",
                    headers=headers(key="bridge-create"),
                    json={
                        "simulation_run_id": scenario.json()["id"],
                        "order_id": order,
                        "title": "Bổ sung cà phê cho SO-001",
                    },
                )
                assert response.status_code == 201, response.text
                case = response.json()
                route = PREFIX + "/decisions/" + case["id"]
                submit = {
                    "revision_id": case["revision"]["id"],
                    "expected_version": case["version"],
                }
                assert (
                    await api.post(
                        route + "/submit", headers=headers(key="bridge-submit"), json=submit
                    )
                ).status_code == 503
                pending = (await api.get(route, headers=headers())).json()
                assert (
                    pending["status"] == "VALIDATING" and pending["workflow_instance_ref"] is None
                )
                # Sources change while the original response is missing. Recover
                # the existing engine instance without dispatching/validating a new plan.
                boundary.facts[boundary.refs["stock"]["data_product_id"]][0]["updated_at"] = (
                    datetime.now(UTC).isoformat()
                )
                retry = await api.post(route + "/reconcile-start", headers=headers(), json={})
                assert retry.status_code == 200, retry.text
                state = retry.json()
                assert len(state["workflow"]["tasks"]) == 2
                assert (
                    len(
                        await engine.find_process(
                            "supply_handover_exception", case["revision"]["id"]
                        )
                    )
                    == 1
                )
                original_complete = engine.complete_task
                lost_task_response = []

                async def complete_with_lost_response(task_id, variables=None):
                    from app.platform.http.errors import CoreError, ErrorCode

                    await original_complete(task_id, variables)
                    if not lost_task_response:
                        lost_task_response.append(task_id)
                        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT)

                engine.complete_task = complete_with_lost_response
                for role in ("warehouse", "logistics"):
                    task = next(t for t in state["workflow"]["tasks"] if t["role"] == role)
                    command = {"revision_id": case["revision"]["id"], "outcome": "confirmed"}
                    # Wrong actor cannot complete the genuine Flowable task.
                    denied = await api.post(
                        route + "/tasks/" + task["id"] + "/complete",
                        headers=headers("manager", "wrong-" + role),
                        json=command,
                    )
                    assert denied.status_code == 403, denied.text
                    accepted = await api.post(
                        route + "/tasks/" + task["id"] + "/complete",
                        headers=headers(role, "confirm-" + role),
                        json=command,
                    )
                    if role == "warehouse":
                        assert accepted.status_code == 503, accepted.text
                        accepted = await api.post(
                            route + "/tasks/" + task["id"] + "/complete",
                            headers=headers(role, "confirm-" + role),
                            json=command,
                        )
                    assert accepted.status_code == 200, accepted.text
                state = accepted.json()
                assert state["case"]["status"] == "PENDING_APPROVAL"
                task = next(t for t in state["workflow"]["tasks"] if t["role"] == "manager")
                stock = boundary.facts[boundary.refs["stock"]["data_product_id"]][0]
                stock["on_hand_quantity"] += 1
                stock["available_quantity"] += 1
                harmless = await api.post(
                    route + "/revalidate", headers=headers("manager"), json={}
                )
                assert harmless.status_code == 200, harmless.text
                assert harmless.json()["impact"]["classification"] == "NO_EFFECT"
                # A genuinely changed original obligation must still block the
                # manager even when the modeled extra supply remains sufficient.
                boundary.facts[boundary.refs["open_orders"]["data_product_id"]][0][
                    "ordered_quantity"
                ] += 1
                approve = {"revision_id": case["revision"]["id"], "outcome": "approved"}
                stale = await api.post(
                    route + "/tasks/" + task["id"] + "/complete",
                    headers=headers("manager", "approve"),
                    json=approve,
                )
                assert stale.status_code == 409, stale.text
                assert stale.json()["detail"]["impact"]["requires_revision"] is True
                checks = await api.get(route + "/checks", headers=headers("manager"))
                assert checks.status_code == 200
                assert any(
                    row["payload"].get("impact", {}).get("requires_revision")
                    for row in checks.json()["items"]
                )
                assert (await engine.get_task(task["id"]))["taskDefinitionKey"] == "managerReview"
                returned = await api.post(
                    route + "/tasks/" + task["id"] + "/complete",
                    headers=headers("manager", "return"),
                    json={
                        **approve,
                        "outcome": "changes_required",
                        "note": "Tồn kho thay đổi, cần tính lại và xác minh phiên bản mới.",
                    },
                )
                assert returned.status_code == 200, returned.text
                assert returned.json()["case"]["status"] == "NEEDS_REVISION"
                async with results.engine.connect() as conn:
                    values = (
                        (
                            await conn.execute(
                                sa.text(
                                    "SELECT outcome FROM data_workspace.decision_checks "
                                    "WHERE case_id=:id"
                                ),
                                {"id": UUID(case["id"])},
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert "STALE" in values and "VALID" in values
                # No planning call or workflow task has written source business rows.
                assert not any("actions" in path for path, _ in calls)
        finally:
            await results.close()


@pytest.mark.asyncio
async def test_source_inbox_dedups_across_users_retains_late_events_and_restart(migrated_workspace):
    from copy import deepcopy
    from datetime import UTC, datetime, timedelta

    from fastapi import HTTPException
    from workspace_persistence_test.decision_repository import DecisionRepository

    url, repository_type = migrated_workspace
    results = repository_type(url)
    repo = DecisionRepository(results.engine)
    org, wh, owner, reviewer, order = [uuid4() for _ in range(5)]
    ctx = SimpleNamespace(org_id=org, owner_id=owner)
    other = SimpleNamespace(org_id=org, owner_id=reviewer)
    run = await results.save("simulation", ctx, {"test": "inbox"})
    case = await repo.create(
        ctx,
        order_id=order,
        warehouse_id=wh,
        simulation_id=UUID(run["id"]),
        title="Theo dõi thay đổi nguồn",
        key="inbox-case",
        roster=[
            {"principal_id": str(actor), "role": role}
            for actor, role in ((owner, "coordinator"), (reviewer, "manager"))
        ],
        payload={"requires_revalidation": True},
    )
    identifier, revision_id = UUID(case["id"]), UUID(case["revision"]["id"])
    aggregate = str(uuid4())

    def source(version):
        return dict(
            id=str(uuid4()),
            producer="warehouse",
            org_id=str(org),
            warehouse_id=str(wh),
            aggregate_id=aggregate,
            aggregate_version=version,
            aggregate_type="reservation",
            event_type="warehouse.order_reserved",
            correlation_id=str(order),
            occurred_at=(datetime.now(UTC) - timedelta(minutes=3 - version)).isoformat(),
            summary_hash="a" * 64,
            source_payload_hash="b" * 64,
            schema_version=1,
        )

    newer, older = source(2), source(1)
    try:
        assert sorted(
            await asyncio.gather(
                repo.observe_source(ctx, identifier, revision_id, newer),
                repo.observe_source(other, identifier, revision_id, newer),
            )
        ) == [False, True]
        assert await repo.observe_source(ctx, identifier, revision_id, older) is True
        assert (await repo.get(ctx, identifier))["status"] == "DRAFT"
        for changed in ({"source_payload_hash": "c" * 64}, {"id": str(uuid4())}):
            with pytest.raises(HTTPException) as conflict:
                await repo.observe_source(ctx, identifier, revision_id, {**newer, **changed})
            assert conflict.value.status_code == 409
        foreign = deepcopy(older)
        foreign["warehouse_id"] = str(uuid4())
        with pytest.raises(HTTPException):
            await repo.observe_source(ctx, identifier, revision_id, foreign)
        await results.close()
        results = repository_type(url)
        repo = DecisionRepository(results.engine)
        assert not await repo.observe_source(other, identifier, revision_id, newer)
        observations = [
            e for e in await repo.timeline(ctx, identifier) if e["event_type"] == "SOURCE_OBSERVED"
        ]
        assert len(observations) == 2
        assert {e["payload"]["aggregate_version"] for e in observations} == {1, 2}
        await repo.record_check(
            ctx, identifier, revision_id, "STALE", {"impact": {"requires_revision": True}}
        )
        assert (await repo.check_history(other, identifier, revision_id))["items"][0][
            "outcome"
        ] == "STALE"
        assert (await repo.get(ctx, identifier))["status"] == "DRAFT"
    finally:
        await results.close()
