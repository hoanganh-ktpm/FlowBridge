from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest_asyncio

from app.core.hasura_provisioner import HasuraMetadataClient, HasuraSettings
from app.core.hasura_provisioner.adapters import EnvironmentSecretResolver
from app.core.hasura_provisioner.schemas import ProductRecord, SourceRecord, TableRef
from app.platform.identity.authentication import VerifiedIdentity

from .fakes import (
    CONTRACT_ID,
    PRODUCT_ID,
    SOURCE_ID,
    Admin,
    Contracts,
    Engine,
    Grants,
    Products,
    Sources,
)


@pytest_asyncio.fixture
async def harness() -> AsyncIterator[SimpleNamespace]:
    settings = HasuraSettings(
        _env_file=None,
        admin_secret="only-a-test-admin-secret",
        allowed_sources={"warehouse"},
        allowed_database_hosts={"warehouse.example.invalid"},
    )
    engine = Engine()
    async with httpx.AsyncClient(transport=httpx.MockTransport(engine)) as http:
        yield SimpleNamespace(
            engine=engine,
            http=http,
            settings=settings,
            client=HasuraMetadataClient(settings, http),
            sources=Sources(
                SourceRecord(
                    id=SOURCE_ID,
                    name="warehouse",
                    secret_ref="warehouse-reader",
                    host="warehouse.example.invalid",
                    database_name="warehouse",
                    db_user="reader",
                    ssl_mode="require",
                    approved=True,
                    revision=1,
                )
            ),
            products=Products(
                ProductRecord(
                    id=PRODUCT_ID,
                    source_id=SOURCE_ID,
                    contract_id=CONTRACT_ID,
                    status="READY",
                    approved=True,
                    table=TableRef(schema="public", name="stock_available"),
                    revision=1,
                )
            ),
            secrets=EnvironmentSecretResolver(
                {"warehouse-reader": "WAREHOUSE_READ_DATABASE_URL"},
                {
                    "WAREHOUSE_READ_DATABASE_URL": "postgresql://reader:test-password@warehouse.example.invalid:5432/warehouse"
                    "?sslmode=require"
                },
            ),
            grants=Grants(),
            contracts=Contracts(),
            identity=Admin(),
            actor=VerifiedIdentity(issuer="https://identity.example.invalid", subject="test-admin"),
        )
