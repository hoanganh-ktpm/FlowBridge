from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa

from app.core.data_sources.discovery import PostgresSchemaDiscovery, canonical_type
from app.core.data_sources.service import ApprovedSourcePolicy, SourceDiscoveryService
from app.core.db import models
from app.core.db.engine import core_engine
from app.core.db.postgres import PostgresCoreStore
from app.core.hasura_provisioner.adapters import EnvironmentSecretResolver
from app.core.hasura_provisioner.schemas import TableRef

from .test_postgres import application, bootstrap, discovery, request

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def submission(
    url,
    department_key,
    app_key,
    *,
    object_name="stock_available",
    columns=None,
    secret_ref="test-source",
):
    fields = columns or [
        {
            "column": "sku",
            "name": "sku",
            "physical_type": "TEXT",
            "wire_type": "string",
            "nullable": False,
            "description": None,
        },
        {
            "column": "available_quantity",
            "name": "available_quantity",
            "physical_type": "INTEGER",
            "wire_type": "integer",
            "nullable": False,
            "description": None,
        },
    ]
    return {
        "manifest": {
            "manifest_version": "1.0",
            "application": {
                "key": app_key,
                "name": "Inventory integration",
                "department_key": department_key,
            },
            "sources": [
                {
                    "key": "pg",
                    "kind": "postgres",
                    "host": url.host,
                    "port": url.port or 5432,
                    "database": url.database,
                    "username": url.username,
                    "ssl_mode": "require",
                    "secret_ref": secret_ref,
                }
            ],
            "data_products": [
                {
                    "key": object_name,
                    "name": "Available stock",
                    "source_key": "pg",
                    "object": {"schema": "public", "name": object_name, "kind": "table"},
                    "fields": [
                        {
                            "column": item["column"],
                            "expected_type": canonical_type(item["physical_type"]),
                        }
                        for item in fields
                    ],
                    "contract_version": "1.0.0",
                    "requested_state": "PUBLISH_REQUESTED",
                }
            ],
        },
        "product_contracts": [
            {
                "product_key": object_name,
                "version": "1.0.0",
                "contract_json": {
                    "document_version": "1.0",
                    "grain": "One row per SKU",
                    "business_key": [fields[0]["column"]],
                    "classification": "INTERNAL",
                    "description": None,
                    "fields": fields,
                    "relationships": [],
                },
            }
        ],
    }


async def test_manifest_to_real_postgres_drafts_without_hasura_bypass(isolated_postgres):
    admin_id, _, department_id = bootstrap(isolated_postgres["core"])
    engine = core_engine(isolated_postgres["core"].render_as_string(hide_password=False))
    store = PostgresCoreStore(engine)
    async with store.transaction() as transaction:
        department = await transaction.get("department", department_id)
    body = submission(
        isolated_postgres["stock"], department.public["code"], "stock-" + uuid4().hex[:10]
    )
    async with httpx.AsyncClient() as http:
        app = application(store, discovery(isolated_postgres["stock"]), admin_id, http)
        validate = await request(app, "POST", "/application-onboarding/validate", json=body)
        assert validate.status_code == 200 and validate.json()["valid"]
        plan = await request(
            app,
            "POST",
            "/application-onboarding/plans",
            json={
                "submission": body,
                "discovery_mode": "LIVE",
            },
        )
        assert plan.status_code == 200, plan.text
        assert plan.json()["valid"]
        assert plan.json()["evidence"] == "LIVE_VERIFIED"
        assert plan.json()["metadata_resource_version"] is None
        assert not plan.json()["ready_for_dispatch"]
        assert any("HASURA_STATE_UNKNOWN" in step["blockers"] for step in plan.json()["steps"])
        key = {"Idempotency-Key": uuid4().hex}
        payload = {
            "submission": body,
            "plan_hash": plan.json()["plan_hash"],
            "expected_application_revision": 0,
        }
        imported = await request(
            app, "POST", "/application-onboarding/imports", json=payload, headers=key
        )
        assert imported.status_code == 201, imported.text
        assert imported.json()["result"] == "DRAFTS_SAVED"
        replay = await request(
            app, "POST", "/application-onboarding/imports", json=payload, headers=key
        )
        assert replay.json() == imported.json()
        async with store.transaction() as transaction:
            product = await transaction.get("product", UUID(imported.json()["data_product_ids"][0]))
            contract = await transaction.get(
                "contract", UUID(imported.json()["contract_version_ids"][0])
            )
            assert product.public["status"] == contract.public["status"] == "DRAFT"
            assert [field["column"] for field in contract.public["contract_json"]["fields"]] == [
                "sku",
                "available_quantity",
            ]
            assert "unit_cost" not in str(contract.public["contract_json"])
            assert await transaction.find("grant", data_product_id=product.public["id"]) == []
            assert await transaction.find("job", data_product_id=product.public["id"]) == []
    await engine.dispose()


async def test_invalid_manifest_and_unavailable_publication_do_not_write(isolated_postgres):
    admin_id, _, _ = bootstrap(isolated_postgres["core"])
    engine = core_engine(isolated_postgres["core"].render_as_string(hide_password=False))
    async with httpx.AsyncClient() as http:
        app = application(
            PostgresCoreStore(engine), discovery(isolated_postgres["stock"]), admin_id, http
        )
        response = await request(
            app, "POST", "/application-onboarding/validate", json={"manifest": {}}
        )
        assert response.status_code == 422
    await engine.dispose()


async def test_access_commands_persist_pending_grants_and_revoke_jobs(isolated_postgres):
    """Fixture ACTIVE metadata tests SQL commands, not actual engine publication."""
    admin_id, _, department_id = bootstrap(isolated_postgres["core"])
    engine = core_engine(isolated_postgres["core"].render_as_string(hide_password=False))
    store = PostgresCoreStore(engine)
    async with store.transaction() as transaction:
        department = await transaction.get("department", department_id)
    body = submission(
        isolated_postgres["stock"], department.public["code"], "grants-" + uuid4().hex[:10]
    )
    async with httpx.AsyncClient() as http:
        app = application(store, discovery(isolated_postgres["stock"]), admin_id, http)
        plan = await request(
            app,
            "POST",
            "/application-onboarding/plans",
            json={"submission": body, "discovery_mode": "LIVE"},
        )
        assert plan.status_code == 200
        imported = await request(
            app,
            "POST",
            "/application-onboarding/imports",
            json={
                "submission": body,
                "plan_hash": plan.json()["plan_hash"],
                "expected_application_revision": 0,
            },
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert imported.status_code == 201, imported.text
        receipt = imported.json()
        product_id = receipt["data_product_ids"][0]
        contract_id = receipt["contract_version_ids"][0]
        async with store.transaction() as transaction:
            for table, identifier, values in (
                (models.applications, receipt["application_id"], {"status": "ACTIVE"}),
                (models.data_sources, receipt["data_source_ids"][0], {"status": "CONNECTED"}),
            ):
                await transaction.connection.execute(
                    table.update().where(table.c.id == UUID(identifier)).values(**values)
                )
        published = await request(
            app,
            "POST",
            "/data-contracts/" + contract_id + "/publish",
            json={"expected_revision": 1},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert published.status_code == 200, published.text
        async with store.transaction() as transaction:
            await transaction.connection.execute(
                models.data_products.update()
                .where(models.data_products.c.id == UUID(product_id))
                .values(status="ACTIVE", active_contract_id=UUID(contract_id))
            )
        scope = {
            "action": "SELECT",
            "columns": ["sku", "available_quantity"],
            "row_scope": {"kind": "ALL"},
        }
        requested = await request(
            app,
            "POST",
            "/access-requests",
            json={
                "data_product_id": product_id,
                "contract_version_id": contract_id,
                "consumer_department_id": str(department_id),
                "consumer_principal_id": None,
                "requested_scope_json": scope,
                "reason": "SQL persistence integration",
            },
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert requested.status_code == 201, requested.text
        approved = await request(
            app,
            "POST",
            "/access-requests/" + requested.json()["id"] + "/approve",
            json={
                "expected_revision": 1,
                "scope_json": scope,
                "expires_at": None,
            },
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert approved.status_code == 202, approved.text
        grant_id = approved.json()["grant"]["id"]
        assert approved.json()["grant"]["status"] == "PENDING_APPLY"
        revoked = await request(
            app,
            "POST",
            "/access-grants/" + grant_id + "/revoke",
            json={
                "expected_desired_revision": 1,
                "reason": "Revoked before engine apply",
            },
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert revoked.status_code == 202, revoked.text
    await engine.dispose()
    fresh_engine = core_engine(isolated_postgres["core"].render_as_string(hide_password=False))
    async with PostgresCoreStore(fresh_engine).transaction() as transaction:
        grant = await transaction.get("grant", UUID(grant_id))
        assert grant.public["status"] == "REVOKING"
        assert grant.public["desired_revision"] == 2 and grant.public["applied_revision"] == 0
        jobs = await transaction.find("job", access_grant_id=grant_id)
        assert len(jobs) == 2
        assert {job.public["job_type"] for job in jobs} == {"APPLY_GRANT", "REVOKE_GRANT"}
        assert (
            await transaction.connection.scalar(
                sa.select(sa.func.count())
                .select_from(models.audit_events)
                .where(models.audit_events.c.resource_ref == grant_id)
            )
            >= 1
        )
    await fresh_engine.dispose()


async def test_two_independent_apps_use_the_same_manifest_onboarding(isolated_postgres):
    admin_id, _, department_id = bootstrap(isolated_postgres["core"])
    engine = core_engine(isolated_postgres["core"].render_as_string(hide_password=False))
    store = PostgresCoreStore(engine)
    bindings = {"owner-test": "OWNER_URL", "consumer-test": "CONSUMER_URL"}
    resolver = EnvironmentSecretResolver(
        bindings,
        {
            "OWNER_URL": isolated_postgres["stock"]
            .set(drivername="postgresql")
            .render_as_string(hide_password=False),
            "CONSUMER_URL": isolated_postgres["consumer"]
            .set(drivername="postgresql")
            .render_as_string(hide_password=False),
        },
    )
    hosts = frozenset({isolated_postgres["stock"].host, isolated_postgres["consumer"].host})
    discovery_service = SourceDiscoveryService(
        PostgresSchemaDiscovery(resolver, hosts),
        ApprovedSourcePolicy(
            resolver,
            hosts,
            {
                "owner-test": (TableRef(schema="public", name="stock_available"),),
                "consumer-test": (TableRef(schema="public", name="order_refs"),),
            },
        ),
    )
    async with store.transaction() as transaction:
        department = await transaction.get("department", department_id)
    consumer_fields = [
        {
            "column": "order_id",
            "name": "order_id",
            "physical_type": "TEXT",
            "wire_type": "string",
            "nullable": False,
            "description": None,
        }
    ]
    payloads = [
        submission(
            isolated_postgres["stock"],
            department.public["code"],
            "warehouse-" + uuid4().hex[:8],
            secret_ref="owner-test",
        ),
        submission(
            isolated_postgres["consumer"],
            department.public["code"],
            "sales-" + uuid4().hex[:8],
            object_name="order_refs",
            columns=consumer_fields,
            secret_ref="consumer-test",
        ),
    ]
    imported_ids = []
    async with httpx.AsyncClient() as http:
        app = application(store, discovery_service, admin_id, http)
        for body in payloads:
            plan = await request(
                app,
                "POST",
                "/application-onboarding/plans",
                json={"submission": body, "discovery_mode": "LIVE"},
            )
            assert plan.status_code == 200 and plan.json()["valid"], plan.text
            response = await request(
                app,
                "POST",
                "/application-onboarding/imports",
                json={
                    "submission": body,
                    "plan_hash": plan.json()["plan_hash"],
                    "expected_application_revision": 0,
                },
                headers={"Idempotency-Key": uuid4().hex},
            )
            assert response.status_code == 201, response.text
            imported_ids.append(response.json()["data_source_ids"][0])
    async with store.transaction() as transaction:
        sources = [await transaction.get("source", UUID(identifier)) for identifier in imported_ids]
        assert sources[0].private["database_name"] != sources[1].private["database_name"]
        assert sources[0].public["application_id"] != sources[1].public["application_id"]
        assert all(source.public["status"] == "DRAFT" for source in sources)
    await engine.dispose()
