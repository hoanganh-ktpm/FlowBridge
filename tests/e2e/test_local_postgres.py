"""Opt-in real local Core + Keycloak + PostgreSQL + Hasura. No mocks or overrides.

Uses an already configured test realm, never changes the realm or old databases.
The synthetic business snapshot is restored after the live-update assertion.
"""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from dotenv import dotenv_values
from pydantic import SecretStr
from scripts.database.engine import make_engine
from scripts.database.local import RUNTIME as LOCAL_RUNTIME
from scripts.database.local import load_local
from scripts.database.products import draft_submissions
from scripts.e2e_runtime import NativeRuntime
from scripts.integration_utils import RUNTIME, tokens, write_env
from sqlalchemy.dialects.postgresql import insert
from tests.e2e.test_automated_onboarding import call, import_manifest, wait_job

from app.core.applications.manifest import source_name
from app.core.data_products.credentials import HasuraJwtCredentials
from app.core.data_products.reading import TrustedHasuraContext
from app.core.db import models

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def local_live(request):
    if os.environ.get("FLOWBRIDGE_LOCAL_E2E") != "1":
        pytest.skip(
            "BLOCKED: set FLOWBRIDGE_LOCAL_E2E=1 after local "
            "PostgreSQL/Hasura and test Keycloak setup"
        )
    load_local()
    identity_file = RUNTIME / "core.env"
    if not identity_file.exists():
        pytest.skip("BLOCKED: existing test Keycloak realm configuration is unavailable")
    identity = {
        k: v for k, v in dotenv_values(identity_file).items() if k.startswith("FLOWBRIDGE_OIDC_")
    }
    state = json.loads((RUNTIME / "secrets.json").read_text())
    if os.environ.get("FLOWBRIDGE_LOCAL_MACHINE_TESTS") == "1":
        from scripts.decision_local_identity import machine_test_identities

        profile = machine_test_identities()
        auth, state, machine_settings = profile.__enter__()
        request.addfinalizer(lambda: profile.__exit__(None, None, None))
        identity.update(machine_settings)
    else:
        auth = tokens()  # Actual Keycloak issuance; legacy human fixtures require MFA policy.
    issuer = identity["FLOWBRIDGE_OIDC_ISSUER"]
    engine = make_engine("core", hide_parameters=True)
    principal_ids, departments = {}, {}
    try:
        with engine.begin() as conn:
            for name in ("it", "sales", "outsider"):
                existing = conn.scalar(
                    sa.select(models.principals.c.id).where(
                        models.principals.c.issuer == issuer,
                        models.principals.c.subject == state["users"][name],
                    )
                )
                identifier = existing or uuid4()
                principal_ids[name] = identifier
                conn.execute(
                    insert(models.principals)
                    .values(
                        id=identifier,
                        issuer=issuer,
                        subject=state["users"][name],
                        kind="service" if name in state.get("service_clients", {}) else "human",
                        service_client_id=state.get("service_clients", {}).get(name),
                        display_name="Local PostgreSQL smoke " + name,
                        platform_role="PLATFORM_ADMIN" if name == "it" else "NONE",
                        status="ACTIVE",
                    )
                    .on_conflict_do_nothing(index_elements=["id"])
                )
            for domain in ("sales", "warehouse", "logistics"):
                existing = conn.scalar(
                    sa.select(models.departments.c.id).where(models.departments.c.code == domain)
                )
                departments[domain] = existing or uuid4()
                conn.execute(
                    insert(models.departments)
                    .values(
                        id=departments[domain],
                        code=domain,
                        name="Local " + domain,
                    )
                    .on_conflict_do_nothing(index_elements=["id"])
                )
            if not conn.scalar(
                sa.select(models.department_memberships.c.id).where(
                    models.department_memberships.c.principal_id == principal_ids["sales"],
                    models.department_memberships.c.department_id == departments["sales"],
                )
            ):
                conn.execute(
                    models.department_memberships.insert().values(
                        id=uuid4(),
                        principal_id=principal_ids["sales"],
                        department_id=departments["sales"],
                        membership_role="STAFF",
                    )
                )
    finally:
        engine.dispose()
    directory = RUNTIME / "runs" / ("local-postgres-" + uuid4().hex[:10])
    directory.mkdir(parents=True)
    (directory / "resources.json").write_text(json.dumps({"imports": {}, "evidence": {}}))
    config = {**identity, **dict(dotenv_values(LOCAL_RUNTIME / "host.env"))}
    config["FLOWBRIDGE_CORE_WORKER_POLL_SECONDS"] = "0.2"
    write_env(directory / "core.env", config)
    runtime = NativeRuntime(directory)
    runtime.clients = {
        name: httpx.Client(
            base_url=runtime.base_url, timeout=40, headers={"Authorization": "Bearer " + auth[name]}
        )
        for name in principal_ids
    }
    runtime.departments = departments
    runtime.principal_ids = principal_ids
    try:
        runtime.start("api")
        runtime.start("worker")
        yield runtime
    finally:
        runtime.close()
        for client in runtime.clients.values():
            client.close()
        if state.get("service_clients"):
            # Keep audit-referenced principals, but revoke the owned test identities.
            engine = make_engine("core", hide_parameters=True)
            try:
                with engine.begin() as conn:
                    conn.execute(
                        models.department_memberships.update()
                        .where(
                            models.department_memberships.c.principal_id.in_(principal_ids.values())
                        )
                        .values(status="REVOKED")
                    )
                    conn.execute(
                        models.principals.update()
                        .where(
                            models.principals.c.id.in_(principal_ids.values()),
                            models.principals.c.kind == "service",
                            models.principals.c.service_client_id.in_(
                                state["service_clients"].values()
                            ),
                        )
                        .values(status="DISABLED")
                    )
            finally:
                engine.dispose()


def test_local_six_views_publish_read_update_revoke(local_live):
    live = local_live
    it, sales, outsider = (live.clients[k] for k in ("it", "sales", "outsider"))
    assert call(it, "GET", "/me")["platform_role"] == "PLATFORM_ADMIN"
    assert call(sales, "GET", "/me")["platform_role"] == "NONE"
    stock_product = stock_contract = stock_source = None
    for domain, body in draft_submissions().items():
        # Imports are idempotent; test can be rerun without replacing live metadata.
        app_id = None
        core = make_engine("core")
        try:
            with core.connect() as conn:
                app = (
                    conn.execute(
                        sa.select(models.applications).where(
                            models.applications.c.app_key == domain
                        )
                    )
                    .mappings()
                    .first()
                )
                if app:
                    app_id = str(app["id"])
        finally:
            core.dispose()
        if app_id is None:
            imported = import_manifest(it, body)
            app_id = imported["application_id"]
        else:
            # Existing approved local imports remain reviewable in Core.
            core = make_engine("core")
            try:
                with core.connect() as conn:
                    imported = {
                        "data_source_ids": [
                            str(i)
                            for i in conn.scalars(
                                sa.select(models.data_sources.c.id).where(
                                    models.data_sources.c.application_id == UUID(app_id)
                                )
                            )
                        ],
                        "data_product_ids": [
                            str(i)
                            for i in conn.scalars(
                                sa.select(models.data_products.c.id).where(
                                    models.data_products.c.data_source_id.in_(
                                        sa.select(models.data_sources.c.id).where(
                                            models.data_sources.c.application_id == UUID(app_id)
                                        )
                                    )
                                )
                            )
                        ],
                    }
            finally:
                core.dispose()
        existing_objects = {
            call(it, "GET", "/data-products/" + key)["object_name"]
            for key in imported["data_product_ids"]
        }
        for declared in body["manifest"]["data_products"]:
            if declared["object"]["name"] in existing_objects:
                continue
            # Add a new read interface through Core commands; never rewrite an active contract.
            product = call(
                it,
                "POST",
                "/data-products",
                201,
                json={
                    "data_source_id": imported["data_source_ids"][0],
                    "product_key": domain + "." + declared["key"],
                    "name": declared["name"],
                    "schema_name": declared["object"]["schema"],
                    "object_name": declared["object"]["name"],
                    "object_type": "VIEW",
                    "classification": "INTERNAL",
                },
            )
            proposal = next(
                c for c in body["product_contracts"] if c["product_key"] == declared["key"]
            )
            call(
                it,
                "POST",
                "/data-products/" + product["id"] + "/contracts",
                201,
                json={"version": proposal["version"], "contract_json": proposal["contract_json"]},
            )
            imported["data_product_ids"].append(product["id"])
        live.resources["imports"][domain] = {"application_id": app_id, **imported}
        live.save()
        app = live.application(app_id)
        if app["status"] == "DRAFT":
            call(
                it,
                "PATCH",
                "/applications/" + app_id,
                json={"expected_revision": app["revision"], "status": "ACTIVE"},
            )
        source_id = imported["data_source_ids"][0]
        source = call(it, "GET", "/data-sources/" + source_id)
        if source["status"] == "VALIDATING":
            # Activation may already enqueue CONNECT_SOURCE.
            wait_job(it, live.source_job(source_id))
        elif source["status"] != "CONNECTED":
            wait_job(
                it,
                call(
                    it,
                    "POST",
                    "/data-sources/" + source_id + "/connect",
                    202,
                    json={"expected_revision": source["revision"]},
                ),
            )
        discovered = call(
            it, "GET", "/data-sources/" + source_id + "/schema", params={"schema_name": "public"}
        )
        assert len(discovered["objects"]) == len(body["manifest"]["data_products"])
        for product_id in imported["data_product_ids"]:
            product = call(it, "GET", "/data-products/" + product_id)
            core = make_engine("core")
            try:
                with core.connect() as conn:
                    contract_id = str(
                        conn.scalar(
                            sa.select(models.data_contract_versions.c.id).where(
                                models.data_contract_versions.c.data_product_id == UUID(product_id)
                            )
                        )
                    )
            finally:
                core.dispose()
            contract = call(it, "GET", "/data-contracts/" + contract_id)
            if contract["status"] == "DRAFT":
                proposal = next(
                    c["contract_json"]
                    for c in body["product_contracts"]
                    if c["product_key"] == product["object_name"][2:]
                )
                if contract["contract_json"] != proposal:
                    contract = call(
                        it,
                        "PATCH",
                        "/data-contracts/" + contract_id,
                        json={"expected_revision": contract["revision"], "contract_json": proposal},
                    )
                call(
                    it,
                    "POST",
                    "/data-contracts/" + contract_id + "/publish",
                    json={"expected_revision": contract["revision"]},
                )
            if product["status"] != "ACTIVE":
                wait_job(
                    it,
                    call(
                        it,
                        "POST",
                        "/data-products/" + product_id + "/publish",
                        202,
                        json={
                            "expected_revision": product["revision"],
                            "contract_version_id": contract_id,
                        },
                    ),
                )
            assert call(it, "GET", "/data-products/" + product_id)["status"] == "ACTIVE"
            if domain == "warehouse" and product["object_name"] == "v_stock_availability":
                stock_product, stock_contract, stock_source = product_id, contract_id, source_id
        live.resources["imports"][domain] = {"application_id": app_id, **imported}
        live.save()
    live.evidence(
        "six_products",
        "PASS: LIVE schema discovery, contracts and verified publication of six views",
    )
    assert stock_product and stock_contract and stock_source
    columns = ["org_id", "warehouse_id", "product_id", "sku", "available_quantity"]
    scope = {"action": "SELECT", "columns": columns, "row_scope": {"kind": "ALL"}}
    core = make_engine("core")
    try:
        with core.connect() as conn:
            previous_grants = conn.scalars(
                sa.select(models.access_grants.c.id)
                .join(
                    models.access_requests,
                    models.access_requests.c.id == models.access_grants.c.access_request_id,
                )
                .where(
                    models.access_grants.c.data_product_id == UUID(stock_product),
                    models.access_grants.c.status == "ACTIVE",
                    models.access_requests.c.reason == "Local PostgreSQL integration smoke",
                )
            ).all()
    finally:
        core.dispose()
    for identifier in previous_grants:
        previous = call(it, "GET", "/access-grants/" + str(identifier))
        wait_job(
            it,
            call(
                it,
                "POST",
                "/access-grants/" + str(identifier) + "/revoke",
                202,
                json={
                    "expected_desired_revision": previous["desired_revision"],
                    "reason": "Resume interrupted local smoke",
                },
            ),
        )
    request = call(
        it,
        "POST",
        "/access-requests",
        201,
        json={
            "data_product_id": stock_product,
            "contract_version_id": stock_contract,
            "consumer_department_id": str(live.departments["sales"]),
            "consumer_principal_id": None,
            "requested_scope_json": scope,
            "reason": "Local PostgreSQL integration smoke",
        },
    )
    approved = call(
        it,
        "POST",
        "/access-requests/" + request["id"] + "/approve",
        202,
        json={"expected_revision": request["revision"], "scope_json": scope, "expires_at": None},
    )
    wait_job(it, approved["job"])
    path = "/data-products/" + stock_product + "/rows"
    params = {"contract_version_id": stock_contract, "columns": ",".join(columns), "limit": 100}
    rows = call(sales, "GET", path, params=params)["rows"]
    engine = make_engine("warehouse", hide_parameters=True)
    with engine.connect() as conn:
        expected = (
            conn.execute(
                sa.text(
                    "SELECT org_id,warehouse_id,product_id,available_quantity "
                    "FROM v_stock_availability"
                )
            )
            .mappings()
            .all()
        )
    assert 0 < len(expected) < 100, "Use a bounded local fixture or add explicit pagination"
    expected_by_key = {
        tuple(str(row[key]) for key in ("org_id", "warehouse_id", "product_id")): Decimal(
            str(row["available_quantity"])
        )
        for row in expected
    }
    actual_by_key = {
        tuple(str(row[key]) for key in ("org_id", "warehouse_id", "product_id")): Decimal(
            str(row["available_quantity"])
        )
        for row in rows
    }
    assert actual_by_key == expected_by_key
    call(outsider, "GET", path, 403, params=params)
    call(it, "GET", path, 403, params=params)
    original = rows[0]
    try:
        # A valid posted receipt line and ledger are updated together in a local
        # transaction. The experiment is restored immediately after live reads.
        with engine.connect() as conn:
            opening = (
                conn.execute(
                    sa.text("""
                SELECT l.id AS line_id,m.id AS movement_id
                FROM goods_receipt_lines l JOIN goods_receipts h ON h.id=l.receipt_id
                JOIN stock_movements m ON m.goods_receipt_line_id=l.id
                WHERE l.product_id=:id AND h.source_type='OPENING_BALANCE'
                AND h.org_id=:org AND h.warehouse_id=:warehouse
            """),
                    {
                        "id": original["product_id"],
                        "org": original["org_id"],
                        "warehouse": original["warehouse_id"],
                    },
                )
                .mappings()
                .one()
            )

        def adjust(delta):
            with engine.begin() as conn:
                conn.execute(
                    sa.text("""UPDATE goods_receipt_lines
                    SET received_quantity=received_quantity+:delta,
                        accepted_quantity=accepted_quantity+:delta WHERE id=:line_id"""),
                    {**opening, "delta": delta},
                )
                conn.execute(
                    sa.text("""UPDATE stock_movements
                    SET quantity_delta=quantity_delta+:delta WHERE id=:movement_id"""),
                    {**opening, "delta": delta},
                )
                conn.execute(
                    sa.text("""UPDATE inventory_balances
                    SET on_hand_quantity=on_hand_quantity+:delta WHERE product_id=:id
                    AND org_id=:org AND warehouse_id=:warehouse"""),
                    {
                        "id": original["product_id"],
                        "delta": delta,
                        "org": original["org_id"],
                        "warehouse": original["warehouse_id"],
                    },
                )

        adjust(1)
        try:
            changed = call(sales, "GET", path, params=params)["rows"]
            updated = next(r for r in changed if r["product_id"] == original["product_id"])
            assert updated["available_quantity"] == original["available_quantity"] + 1
        finally:
            adjust(-1)
    finally:
        engine.dispose()
    live.evidence(
        "source_update",
        "PASS: Sales read current Warehouse NUMERIC availability via governed Hasura view",
    )
    grant = call(it, "GET", "/access-grants/" + approved["grant"]["id"])
    context = TrustedHasuraContext(
        live.principal_ids["sales"],
        live.departments["sales"],
        UUID(stock_product),
        UUID(stock_contract),
        UUID(grant["id"]),
        grant["desired_revision"],
        grant["applied_revision"],
        datetime.now(UTC),
        None,
    )
    credentials = HasuraJwtCredentials(
        SecretStr(live.env["FLOWBRIDGE_CORE_HASURA_JWT_SIGNING_KEY"]),
        "flowbridge-core",
        "flowbridge-data",
    )
    token = asyncio.run(credentials.issue(context)).get_secret_value()
    query = {
        "query": "{"
        + source_name("warehouse", "pg")
        + "{v_stock_availability{sku available_quantity}}}"
    }
    graphql = live.env["FLOWBRIDGE_HASURA_ENDPOINT"] + "/v1/graphql"
    with httpx.Client(timeout=20) as client:
        assert "errors" in client.post(graphql, json=query).json()
        assert (
            "data"
            in client.post(graphql, json=query, headers={"Authorization": "Bearer " + token}).json()
        )
    revoked = call(
        it,
        "POST",
        "/access-grants/" + grant["id"] + "/revoke",
        202,
        json={
            "expected_desired_revision": grant["desired_revision"],
            "reason": "Local smoke complete",
        },
    )
    wait_job(it, revoked)
    call(sales, "GET", path, 403, params=params)
    with httpx.Client(timeout=20) as client:
        denied = client.post(
            graphql, json=query, headers={"Authorization": "Bearer " + token}
        ).json()
        assert "errors" in denied and not denied.get("data")
    live.evidence(
        "read_grant_revoke", "PASS: approved real GraphQL read, outsider/IT denied, revoke denied"
    )


@pytest.mark.asyncio
async def test_all_workspace_planning_roles_read_through_real_core(local_live):
    """Workspace HTTP adapter reads all nine published roles, not raw owner databases."""
    import importlib.util
    import sys
    from pathlib import Path

    from scripts.database.fixtures.business import ORG_ID, WAREHOUSE_ID

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "workspace_live_sources",
        root / "services/data-workspace-api/app/__init__.py",
        submodule_search_locations=[str(root / "services/data-workspace-api/app")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    from workspace_live_sources.connector import CoreConnector
    from workspace_live_sources.handoff_readiness import preparation_policy
    from workspace_live_sources.schemas import ProductRef
    from workspace_live_sources.source_integrity import validate_planning_facts

    live, grants, facts = local_live, [], {}
    it = live.clients["it"]
    required = {
        "demand": "sales.planning_demand_daily",
        "stock": "warehouse.stock_availability",
        "inbound": "warehouse.inbound_schedule",
        "open_orders": "sales.open_orders",
        "commitments": "warehouse.order_commitments",
        "catalog": "warehouse.planning_catalog",
        "receipts": "warehouse.receipts_daily",
        "schedule": "logistics.handoff_schedule",
        "capacity": "logistics.handoff_capacity",
        "preparation": "warehouse.handoff_preparation_policy",
    }
    refs = {}
    try:
        for imported in live.resources["imports"].values():
            for identifier in imported["data_product_ids"]:
                product = call(it, "GET", "/data-products/" + identifier)
                if product["name"] not in required.values():
                    continue
                contract = call(it, "GET", "/data-contracts/" + product["active_contract_id"])
                scope = {
                    "action": "SELECT",
                    "columns": [f["column"] for f in contract["contract_json"]["fields"]],
                    "row_scope": {"kind": "ALL"},
                }
                request = call(
                    it,
                    "POST",
                    "/access-requests",
                    201,
                    json={
                        "data_product_id": identifier,
                        "contract_version_id": contract["id"],
                        "consumer_department_id": str(live.departments["sales"]),
                        "consumer_principal_id": None,
                        "requested_scope_json": scope,
                        "reason": "Decision Workspace local source acceptance",
                    },
                )
                approved = call(
                    it,
                    "POST",
                    "/access-requests/" + request["id"] + "/approve",
                    202,
                    json={
                        "expected_revision": request["revision"],
                        "scope_json": scope,
                        "expires_at": (datetime.now(UTC) + timedelta(minutes=15))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    },
                )
                grants.append(approved["grant"]["id"])
                wait_job(it, approved["job"])
                refs[product["name"]] = ProductRef(
                    data_product_id=identifier, contract_version_id=contract["id"]
                )
        assert set(refs) == set(required.values())
        async with httpx.AsyncClient(timeout=30) as http:
            connector = CoreConnector(http, live.base_url, ORG_ID)
            caller = live.clients["sales"].headers["Authorization"].partition(" ")[2]
            ctx = await connector.context(caller)
            async with connector.request_scope():
                for role, name in required.items():
                    _, facts[role] = await connector.rows(ctx, refs[name])
                    assert all(str(row["org_id"]) == str(ORG_ID) for row in facts[role])
            units = validate_planning_facts(
                facts["stock"],
                facts["inbound"],
                facts["open_orders"],
                facts["commitments"],
                WAREHOUSE_ID,
                facts["catalog"],
            )
            assert units and facts["open_orders"] and facts["catalog"] and facts["receipts"]
            policy = preparation_policy(facts["preparation"], WAREHOUSE_ID)
            assert policy is None  # New nullable policy fields are not an implicit zero buffer.
        live.evidence(
            "workspace_sources",
            {
                "status": "PASS",
                "identity": "real service accounts",
                "roles": {role: len(rows) for role, rows in facts.items()},
            },
        )
    finally:
        for identifier in grants:
            grant = call(it, "GET", "/access-grants/" + identifier)
            if grant["status"] not in {"REVOKED", "EXPIRED"}:
                result = call(
                    it,
                    "POST",
                    "/access-grants/" + identifier + "/revoke",
                    202,
                    json={
                        "expected_desired_revision": grant["desired_revision"],
                        "reason": "Decision Workspace local source test complete",
                    },
                )
                wait_job(it, result)
