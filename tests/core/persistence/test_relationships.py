import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from tests.core.runtime.fakes import contract_document

from app.core.db import models
from app.core.db.engine import core_engine
from app.core.db.postgres import PostgresCoreStore
from app.core.hasura_provisioner.settings import HasuraSettings
from app.core.hasura_provisioner.worker import PublicationWorker, target_of

from .test_postgres import application, bootstrap, discovery, request

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_relationship_registry_migration_receipt_restart_and_job_claim(isolated_postgres):
    admin_id, _, department_id = bootstrap(isolated_postgres["core"])
    app_id, source_id, product_id, contract_id = (uuid4() for _ in range(4))
    source_url = isolated_postgres["stock"]
    sync = sa.create_engine(isolated_postgres["core"], hide_parameters=True)
    try:
        with sync.begin() as connection:
            connection.execute(
                models.applications.insert().values(
                    id=app_id,
                    department_id=department_id,
                    app_key="relationships-" + app_id.hex[:10],
                    name="Relationship integration fixture",
                    status="ACTIVE",
                )
            )
            connection.execute(
                models.data_sources.insert().values(
                    id=source_id,
                    application_id=app_id,
                    source_key="relations." + source_id.hex,
                    host=source_url.host,
                    port=source_url.port,
                    database_name=source_url.database,
                    db_user=source_url.username,
                    secret_ref="test-source",
                    ssl_mode="require",
                    hasura_source_name="relations_" + source_id.hex,
                    status="CONNECTED",
                )
            )
            connection.execute(
                models.data_products.insert().values(
                    id=product_id,
                    data_source_id=source_id,
                    product_key="relations." + product_id.hex,
                    name="Relationship integration product",
                    schema_name="public",
                    object_name="stock_available",
                    object_type="TABLE",
                    classification="INTERNAL",
                )
            )
            connection.execute(
                models.data_contract_versions.insert().values(
                    id=contract_id,
                    data_product_id=product_id,
                    version="1.0.0",
                    contract_json=contract_document(),
                    status="PUBLISHED",
                    published_at=sa.func.now(),
                    published_by=admin_id,
                )
            )
            connection.execute(
                models.data_products.update()
                .where(models.data_products.c.id == product_id)
                .values(status="ACTIVE", active_contract_id=contract_id)
            )
    finally:
        sync.dispose()
    engine = core_engine(isolated_postgres["core"].render_as_string(hide_password=False))
    other_engine = core_engine(isolated_postgres["core"].render_as_string(hide_password=False))
    try:
        async with httpx.AsyncClient() as http:
            app = application(PostgresCoreStore(engine), discovery(source_url), admin_id, http)
            body = {
                "name": "same_product",
                "source_product_id": str(product_id),
                "target_product_id": str(product_id),
                "relationship_type": "object",
                "field_mapping": {"sku": "sku"},
            }
            headers = {"Idempotency-Key": uuid4().hex}
            responses = await asyncio.gather(
                *(
                    request(app, "POST", "/relationships", json=body, headers=headers)
                    for _ in range(2)
                )
            )
            assert [response.status_code for response in responses] == [201, 201]
            assert responses[0].json() == responses[1].json()
            relationship_id = responses[0].json()["id"]
            restarted = application(
                PostgresCoreStore(other_engine), discovery(source_url), admin_id, http
            )
            response = await request(restarted, "GET", "/relationships/" + relationship_id)
            assert response.status_code == 200 and response.json()["field_mapping"] == {
                "sku": "sku"
            }
            response = await request(
                restarted,
                "POST",
                "/relationships/" + relationship_id + "/publish",
                json={"expected_revision": 1},
                headers={"Idempotency-Key": uuid4().hex},
            )
            assert response.status_code == 202
            first = PublicationWorker(app.state.core, None, HasuraSettings(_env_file=None))
            second = PublicationWorker(restarted.state.core, None, HasuraSettings(_env_file=None))
            leases = await asyncio.gather(
                first.claim("relationship-a"), second.claim("relationship-b")
            )
            assert sum(lease is not None for lease in leases) == 1
            async with restarted.state.core.store.transaction(read_only=True) as transaction:
                job = await transaction.get("job", UUID(response.json()["job_id"]))
                assert job.public["status"] == "RUNNING"
                assert target_of(job.public) == ("relationship", UUID(relationship_id))
                assert (
                    len(await transaction.find("relationship", source_product_id=str(product_id)))
                    == 1
                )
    finally:
        await engine.dispose()
        await other_engine.dispose()
