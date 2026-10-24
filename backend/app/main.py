from dataclasses import dataclass
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, Request

from app.core.applications.onboarding import MissingIntegration
from app.core.applications.runtime import RuntimeOnboarding
from app.core.data_products.querying import RelationshipQueries
from app.core.data_products.reading import DataReader
from app.core.data_products.relationships import RelationshipRegistry
from app.core.data_sources.service import SourceDiscoveryService
from app.core.db.ports import CoreStore
from app.core.governance.service import DataSharingService
from app.core.hasura_provisioner.errors import (
    HasuraError,
    HasuraTimeout,
    MetadataConflict,
    ProvisioningDenied,
)
from app.core.router import router
from app.platform.http.correlation import CorrelationMiddleware
from app.platform.http.errors import ErrorCode, error_response, register_exception_handlers
from app.platform.http.request_policy import PrivateJsonMiddleware
from app.platform.identity.settings import IdentitySettings
from app.platform.workflow.router import WorkflowRuntime, workflow_router


@dataclass(frozen=True)
class CoreDependencies:
    store: CoreStore
    discovery: SourceDiscoveryService
    reader: DataReader | None
    http: httpx.AsyncClient
    identity_settings: IdentitySettings
    evidence: object | None = None
    metadata: object | None = None
    workflow: WorkflowRuntime | None = None


def create_app(dependencies: CoreDependencies | None = None, *, testing: bool = False) -> FastAPI:
    if dependencies is None or (not testing and not dependencies.store.durable):
        raise MissingIntegration(
            "Configure a durable CoreStore; no production memory fallback exists"
        )
    if (
        not testing
        and dependencies.workflow is not None
        and (
            not getattr(dependencies.workflow.registry, "durable", False)
            or not getattr(dependencies.workflow.event_router.inbox, "durable", False)
        )
    ):
        raise MissingIntegration("Workflow requires durable action registry and event inbox")
    settings = dependencies.identity_settings
    if not testing and not all(
        (
            settings.oidc_issuer,
            settings.oidc_audience,
            settings.oidc_introspection_url,
            settings.oidc_client_id,
            settings.oidc_client_secret,
        )
    ):
        raise MissingIntegration("Configure trusted OIDC introspection before enabling Core APIs")
    app = FastAPI(title="FlowBridge Core P0", version="0.2.0")
    app.state.settings = settings
    app.state.resources = SimpleNamespace(http=dependencies.http)
    app.state.core = DataSharingService(
        dependencies.store, dependencies.discovery, dependencies.reader, dependencies.evidence
    )
    app.state.onboarding = RuntimeOnboarding(app.state.core, dependencies.metadata)
    app.state.relationships = RelationshipRegistry(app.state.core, dependencies.metadata)
    app.state.relationship_queries = RelationshipQueries(app.state.core, dependencies.metadata)
    app.state.workflow = dependencies.workflow
    register_exception_handlers(app)

    async def hasura_error(request: Request, error: HasuraError):
        code = (
            ErrorCode.UPSTREAM_TIMEOUT
            if isinstance(error, HasuraTimeout)
            else ErrorCode.VERSION_CONFLICT
            if isinstance(error, MetadataConflict)
            else ErrorCode.FORBIDDEN
            if isinstance(error, ProvisioningDenied)
            else ErrorCode.UPSTREAM_UNAVAILABLE
        )
        return error_response(request, code)

    app.add_exception_handler(HasuraError, hasura_error)
    from app.core.data_sources.discovery import DiscoveryError

    async def discovery_error(request: Request, error: DiscoveryError):
        return error_response(request, ErrorCode.UPSTREAM_UNAVAILABLE)

    app.add_exception_handler(DiscoveryError, discovery_error)
    app.add_middleware(PrivateJsonMiddleware)
    app.add_middleware(CorrelationMiddleware)
    app.include_router(router)
    app.include_router(workflow_router)
    return app
