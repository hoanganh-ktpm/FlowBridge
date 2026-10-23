"""Native Core HTTP/worker composition; no migrations or fake persistence at startup."""

import argparse
import asyncio
import logging
import os
import ssl
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.data_products.credentials import HasuraJwtCredentials
from app.core.data_products.reading import HasuraDataReader
from app.core.data_sources.discovery import PostgresSchemaDiscovery
from app.core.data_sources.service import ApprovedSourcePolicy, SourceDiscoveryService
from app.core.db.engine import core_engine
from app.core.db.postgres import PostgresCoreStore
from app.core.hasura_provisioner.adapters import EnvironmentSecretResolver
from app.core.hasura_provisioner.client import HasuraMetadataClient
from app.core.hasura_provisioner.schemas import TableRef
from app.core.hasura_provisioner.settings import HasuraSettings
from app.core.hasura_provisioner.worker import PublicationWorker
from app.main import CoreDependencies
from app.main import create_app as compose_app
from app.platform.http.errors import CoreError
from app.platform.identity.settings import IdentitySettings


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLOWBRIDGE_CORE_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    database_url: SecretStr = Field(
        repr=False, validation_alias=AliasChoices("FLOWBRIDGE_CORE_DATABASE_URL", "DXOS_CORE_URL")
    )
    source_scopes: dict[str, tuple[TableRef, ...]] = Field(default_factory=dict)
    worker_poll_seconds: float = Field(default=2, ge=0.2, le=60)
    verified_hasura_auth_profile: bool = False
    hasura_jwt_signing_key: SecretStr | None = Field(default=None, repr=False)
    hasura_jwt_issuer: str = "flowbridge-core"
    hasura_jwt_audience: str = "flowbridge-data"


def dependencies():
    runtime = RuntimeSettings()
    identity = IdentitySettings()
    settings = HasuraSettings()
    engine = core_engine(runtime.database_url.get_secret_value())
    tls = (
        ssl.create_default_context(cafile=str(identity.oidc_ca_bundle))
        if identity.oidc_ca_bundle
        else True
    )
    http = httpx.AsyncClient(timeout=settings.timeout_seconds, follow_redirects=False, verify=tls)
    secrets = EnvironmentSecretResolver(
        settings.secret_bindings, {**dotenv_values(".env"), **os.environ}
    )
    policy = ApprovedSourcePolicy(secrets, settings.allowed_database_hosts, runtime.source_scopes)
    discovery = SourceDiscoveryService(
        PostgresSchemaDiscovery(secrets, settings.allowed_database_hosts), policy
    )
    metadata = (
        HasuraMetadataClient(settings, http) if settings.admin_secret.get_secret_value() else None
    )
    reader = None
    if runtime.verified_hasura_auth_profile:
        if runtime.hasura_jwt_signing_key is None:
            raise ValueError("A verified Hasura auth profile needs its explicit signing key")
        credentials = HasuraJwtCredentials(
            runtime.hasura_jwt_signing_key, runtime.hasura_jwt_issuer, runtime.hasura_jwt_audience
        )
        reader = HasuraDataReader(http, settings, credentials, verified_auth_profile=True)
    bundle = CoreDependencies(
        PostgresCoreStore(engine), discovery, reader, http, identity, metadata=metadata
    )
    return bundle, engine, http, settings, runtime


def create_app():
    bundle, engine, http, _, _ = dependencies()
    app = compose_app(bundle)

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            await http.aclose()
            await engine.dispose()

    app.router.lifespan_context = lifespan
    return app


async def run_worker():
    bundle, engine, http, settings, runtime = dependencies()
    try:
        if bundle.metadata is None:
            raise ValueError("Configure a real Hasura Metadata API before running the worker")
        app = compose_app(bundle)
        worker = PublicationWorker(app.state.core, bundle.metadata, settings)
        worker_id = "core-worker-" + uuid4().hex
        while True:
            try:
                await worker.run_once(worker_id)
            except CoreError as error:
                logging.getLogger(__name__).warning(
                    "Worker dependency failure: %s", error.code.value
                )
            await asyncio.sleep(runtime.worker_poll_seconds)
    finally:
        await http.aclose()
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="FlowBridge native Core process")
    parser.add_argument("process", choices=["worker"])
    parser.parse_args()
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
