import asyncio
import json
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select

from app.core.data_sources.discovery import PostgresSchemaDiscovery
from app.core.data_sources.service import ApprovedSourcePolicy, SourceDiscoveryService
from app.core.db import models
from app.core.db.engine import core_engine
from app.core.db.ports import CommandReceipt, StoredResource
from app.core.db.postgres import PostgresCoreStore
from app.core.hasura_provisioner.adapters import EnvironmentSecretResolver
from app.core.hasura_provisioner.schemas import TableRef
from app.main import CoreDependencies, create_app
from app.platform.identity.authentication import VerifiedIdentity, authenticated_identity
from app.platform.identity.settings import IdentitySettings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ISSUER = "https://identity.example.test"


def bootstrap(url):
    admin_id, owner_id, department_id = uuid4(), uuid4(), uuid4()
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            for principal_id, role in ((admin_id, "PLATFORM_ADMIN"), (owner_id, "NONE")):
                connection.execute(
                    models.principals.insert().values(
                        id=principal_id,
                        issuer=ISSUER,
                        subject=str(principal_id),
                        kind="human",
                        platform_role=role,
                    )
                )
            connection.execute(
                models.departments.insert().values(
                    id=department_id,
                    code="dept-" + uuid4().hex[:10],
                    name="Integration department",
                )
            )
            connection.execute(
                models.department_memberships.insert().values(
                    id=uuid4(),
                    principal_id=owner_id,
                    department_id=department_id,
                    membership_role="OWNER",
                )
            )
    finally:
        engine.dispose()
    return admin_id, owner_id, department_id


async def test_concurrent_http_retries_share_one_durable_receipt(isolated_postgres):
    admin_id, _, department_id = bootstrap(isolated_postgres["core"])
    engine = core_engine(isolated_postgres["core"].render_as_string(hide_password=False))
    async with httpx.AsyncClient() as http:
        app = application(
            PostgresCoreStore(engine), discovery(isolated_postgres["stock"]), admin_id, http
        )
        body = {
            "department_id": str(department_id),
            "app_key": "concurrent-" + uuid4().hex[:10],
            "name": "Concurrent retry integration",
        }
        headers = {"Idempotency-Key": uuid4().hex}
        responses = await asyncio.gather(
            *(request(app, "POST", "/applications", json=body, headers=headers) for _ in range(2))
        )
        assert [response.status_code for response in responses] == [201, 201]
        assert responses[0].json() == responses[1].json()
        async with app.state.core.store.transaction() as transaction:
            rows = await transaction.find("application", app_key=body["app_key"])
            assert len(rows) == 1
    await engine.dispose()


def discovery(url):
    raw = url.set(drivername="postgresql").render_as_string(hide_password=False)
    resolver = EnvironmentSecretResolver({"test-source": "SOURCE_URL"}, {"SOURCE_URL": raw})
    hosts = frozenset({url.host})
    return SourceDiscoveryService(
        PostgresSchemaDiscovery(resolver, hosts),
        ApprovedSourcePolicy(
            resolver, hosts, {"test-source": (TableRef(schema="public", name="stock_available"),)}
        ),
    )


def application(store, discovery_service, principal_id, http):
    app = create_app(
        CoreDependencies(
            store,
            discovery_service,
            None,
            http,
            IdentitySettings(
                _env_file=None,
                oidc_issuer=ISSUER,
                oidc_audience="core",
                oidc_introspection_url="https://identity.example.test/introspect",
                oidc_client_id="core",
                oidc_client_secret="SYNTHETIC_TEST_BOUNDARY",
            ),
        )
    )

    async def verified_test_boundary():
        return VerifiedIdentity(issuer=ISSUER, subject=str(principal_id))

    app.dependency_overrides[authenticated_identity] = verified_test_boundary
    return app


async def request(app, method, path, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://core.test"
    ) as client:
        return await client.request(method, "/api/v1/core" + path, **kwargs)


async def test_api_persistence_restart_and_replay(isolated_postgres):
    admin_id, _, department_id = bootstrap(isolated_postgres["core"])
    url = isolated_postgres["core"].render_as_string(hide_password=False)
    engine = core_engine(url)
    service = discovery(isolated_postgres["stock"])
    payload = {
        "department_id": str(department_id),
        "app_key": "app-" + uuid4().hex[:10],
        "name": "Independent application",
    }
    headers = {"Idempotency-Key": uuid4().hex}
    async with httpx.AsyncClient() as http:
        app = application(PostgresCoreStore(engine), service, admin_id, http)
        response = await request(app, "POST", "/applications", json=payload, headers=headers)
        assert response.status_code == 201, response.text
        original = response.json()
        await engine.dispose()
        program = """
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests/core/persistence"))
import httpx
from test_postgres import application, request
from app.core.db.engine import core_engine
from app.core.db.postgres import PostgresCoreStore
async def main():
    values = json.load(sys.stdin)
    engine = core_engine(values["url"])
    async with httpx.AsyncClient() as http:
        app = application(PostgresCoreStore(engine), None, values["admin"], http)
        response = await request(
            app, "POST", "/applications", json=values["payload"], headers=values["headers"]
        )
        print(json.dumps({"status": response.status_code, "body": response.json()}))
    await engine.dispose()
asyncio.run(main())
"""
        child = subprocess.run(
            [sys.executable, "-c", program],
            input=json.dumps(
                {"url": url, "admin": str(admin_id), "payload": payload, "headers": headers}
            ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert child.returncode == 0, child.stderr
        child_result = json.loads(child.stdout)
        assert child_result == {"status": 201, "body": original}
        engine = core_engine(url)
        restarted = application(PostgresCoreStore(engine), service, admin_id, http)
        replay = await request(restarted, "POST", "/applications", json=payload, headers=headers)
        assert replay.status_code == 201 and replay.json() == original
        conflict = await request(
            restarted, "POST", "/applications", json={**payload, "name": "Other"}, headers=headers
        )
        assert conflict.status_code == 409
        async with PostgresCoreStore(engine).transaction() as transaction:
            records = await transaction.find("application", app_key=payload["app_key"])
            assert len(records) == 1 and records[0].public["id"] == original["id"]
        assert payload["app_key"] in replay.text
    await engine.dispose()


async def test_owner_cannot_administer_platform(isolated_postgres):
    _, owner_id, department_id = bootstrap(isolated_postgres["core"])
    engine = core_engine(isolated_postgres["core"].render_as_string(hide_password=False))
    async with httpx.AsyncClient() as http:
        app = application(
            PostgresCoreStore(engine), discovery(isolated_postgres["stock"]), owner_id, http
        )
        response = await request(
            app,
            "POST",
            "/applications",
            json={"department_id": str(department_id), "app_key": "forbidden", "name": "Forbidden"},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert response.status_code == 403
    await engine.dispose()


async def test_rollback_keeps_audit_and_receipt_atomic(isolated_postgres):
    admin_id, _, department_id = bootstrap(isolated_postgres["core"])
    engine = core_engine(isolated_postgres["core"].render_as_string(hide_password=False))
    store = PostgresCoreStore(engine)
    identifier = uuid4()
    key = uuid4().hex
    with pytest.raises(RuntimeError, match="rollback"):
        async with store.transaction() as transaction:
            await transaction.insert(
                "application",
                StoredResource(
                    {
                        "id": str(identifier),
                        "department_id": str(department_id),
                        "app_key": "atomic-" + uuid4().hex[:10],
                        "name": "Atomic",
                        "status": "DRAFT",
                    },
                    {},
                ),
            )
            await transaction.audit(
                admin_id, "integration", "create", identifier, resource_type="application"
            )
            await transaction.save_receipt(
                key, CommandReceipt("a" * 64, 201, {"id": str(identifier)})
            )
            raise RuntimeError("rollback")
    async with store.transaction() as transaction:
        assert await transaction.receipt(key) is None
        assert await transaction.find("application", id=str(identifier)) == []
        count = await transaction.connection.scalar(
            select(func.count())
            .select_from(models.audit_events)
            .where(models.audit_events.c.resource_ref == str(identifier))
        )
        assert count == 0
    await engine.dispose()


async def test_real_postgres_discovery_has_extra_unpublished_column(isolated_postgres):
    url = isolated_postgres["stock"]
    service = discovery(url)
    snapshot = await service.snapshot(
        StoredResource(
            {
                "id": str(uuid4()),
                "source_key": "newapp.pg",
                "hasura_source_name": "newapp_source",
                "status": "DRAFT",
                "revision": 1,
            },
            {
                "host": url.host,
                "port": url.port or 5432,
                "database_name": url.database,
                "db_user": url.username,
                "ssl_mode": url.query.get("sslmode", "verify-full"),
                "secret_ref": "test-source",
            },
        )
    )
    found = snapshot.objects[0]
    assert found.primary_key == ("sku",)
    assert {column.name for column in found.columns} == {"sku", "available_quantity", "unit_cost"}


async def test_permission_lock_is_shared_between_store_instances(isolated_postgres):
    url = isolated_postgres["core"].render_as_string(hide_password=False)
    first_engine, second_engine = core_engine(url), core_engine(url)
    first, second = PostgresCoreStore(first_engine), PostgresCoreStore(second_engine)
    product, department = uuid4(), uuid4()
    entered = asyncio.Event()

    async def competitor():
        async with second.permission_lock(product, department):
            entered.set()

    async with first.permission_lock(product, department):
        task = asyncio.create_task(competitor())
        await asyncio.sleep(0.5)
        assert not entered.is_set()
    await asyncio.wait_for(task, timeout=15)
    assert entered.is_set()
    await first_engine.dispose()
    await second_engine.dispose()
