import asyncio
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from app.core.db.commands import timestamp
from app.core.db.engine import core_engine
from app.core.db.ports import StoredResource
from app.core.db.postgres import PostgresCoreStore
from app.core.hasura_provisioner.settings import HasuraSettings
from app.core.hasura_provisioner.worker import LeaseLost, PublicationWorker

from .test_postgres import application, bootstrap, discovery, request

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_workers_claim_distinct_persisted_jobs(isolated_postgres):
    admin_id, _, department_id = bootstrap(isolated_postgres["core"])
    url = isolated_postgres["core"].render_as_string(hide_password=False)
    engine = core_engine(url)
    async with httpx.AsyncClient() as http:
        app = application(
            PostgresCoreStore(engine), discovery(isolated_postgres["stock"]), admin_id, http
        )
        app_key = "jobs-" + uuid4().hex[:10]
        response = await request(
            app,
            "POST",
            "/applications",
            json={
                "department_id": str(department_id),
                "app_key": app_key,
                "name": "Worker integration",
            },
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert response.status_code == 201, response.text
        application_id = response.json()["id"]
        response = await request(
            app,
            "PATCH",
            "/applications/" + application_id,
            json={"expected_revision": 1, "status": "ACTIVE"},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert response.status_code == 200, response.text
        source_url = isolated_postgres["stock"]
        for suffix in ("one", "two"):
            response = await request(
                app,
                "POST",
                "/data-sources",
                json={
                    "application_id": application_id,
                    "source_key": app_key + "." + suffix,
                    "db_type": "POSTGRESQL",
                    "host": source_url.host,
                    "port": source_url.port,
                    "database_name": source_url.database,
                    "db_user": source_url.username,
                    "ssl_mode": "require",
                    "secret_ref": "test-source",
                },
                headers={"Idempotency-Key": uuid4().hex},
            )
            assert response.status_code == 201, response.text
            response = await request(
                app,
                "POST",
                "/data-sources/" + response.json()["id"] + "/connect",
                json={"expected_revision": 1},
                headers={"Idempotency-Key": uuid4().hex},
            )
            assert response.status_code == 202, response.text
        other_engine = core_engine(url)
        other = application(PostgresCoreStore(other_engine), discovery(source_url), admin_id, http)
        first = PublicationWorker(app.state.core, None, HasuraSettings(_env_file=None))
        second = PublicationWorker(other.state.core, None, HasuraSettings(_env_file=None))
        claims = await asyncio.gather(first.claim("worker-a"), second.claim("worker-b"))
        if claims[0] is None:
            claims[0] = await first.claim("worker-a")
        if claims[1] is None:
            claims[1] = await second.claim("worker-b")
        assert all(claim is not None for claim in claims)
        assert claims[0].job_id != claims[1].job_id
        async with app.state.core.store.transaction() as transaction:
            job = await transaction.get("job", claims[0].job_id)
            await transaction.replace(
                "job",
                StoredResource(
                    job.public,
                    {
                        **job.private,
                        "lease_expires_at": timestamp(
                            await transaction.now() - timedelta(seconds=1)
                        ),
                    },
                ),
            )
        replacement = await first.claim("worker-c")
        assert replacement.job_id == claims[0].job_id
        assert replacement.attempt == claims[0].attempt + 1
        with pytest.raises(LeaseLost):
            await first.renew(claims[0])
        await other_engine.dispose()
    await engine.dispose()
