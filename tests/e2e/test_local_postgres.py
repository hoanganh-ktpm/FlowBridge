"""Opt-in real local Core + Keycloak + PostgreSQL + Hasura. No mocks or overrides.

Uses an already configured test realm, never changes the realm or old databases.
The synthetic business snapshot is restored after the live-update assertion.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
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
from scripts.docker_env import write_env
from scripts.e2e_environment import RUNTIME, tokens
from scripts.e2e_runtime import NativeRuntime
from sqlalchemy.dialects.postgresql import insert
from tests.e2e.test_automated_onboarding import call, import_manifest, wait_job

from app.core.applications.manifest import source_name
from app.core.data_products.credentials import HasuraJwtCredentials
from app.core.data_products.reading import TrustedHasuraContext
from app.core.db import models

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def local_live():
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
    auth = tokens()  # Actual Keycloak token issuance and introspection, no realm mutations.
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
                        kind="human",
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
        assert len(discovered["objects"]) == 2
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
    assert len(rows) == 8
    call(outsider, "GET", path, 403, params=params)
    call(it, "GET", path, 403, params=params)
    engine = make_engine("warehouse", hide_parameters=True)
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
            """),
                    {"id": original["product_id"]},
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
                    SET on_hand_quantity=on_hand_quantity+:delta WHERE product_id=:id"""),
                    {"id": original["product_id"], "delta": delta},
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
