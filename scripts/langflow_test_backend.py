# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Explicit local test entry point; the normal backend never imports this module.
import argparse
import asyncio
import ipaddress
import json
import secrets
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import sqlalchemy as sa  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import APIRouter, Depends, FastAPI, Request  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from flowbridge.db import create_engine, create_session_factory, get_session  # noqa: E402
from flowbridge.identity.authentication import (  # noqa: E402
    VerifiedIdentity,
    authenticated_identity,
)
from flowbridge.main import create_app  # noqa: E402
from flowbridge.operations.router import router as operations_router  # noqa: E402
from flowbridge.settings import Settings  # noqa: E402
from flowbridge.shared.errors import CoreError, ErrorCode  # noqa: E402
from flowbridge.supplier_assistant.policy import SupplierPolicy  # noqa: E402
from flowbridge.supplier_assistant.prediction.router import (  # noqa: E402
    model_readiness,
)
from flowbridge.supplier_assistant.prediction.router import (  # noqa: E402
    router as prediction_router,
)
from flowbridge.supplier_assistant.repository import SupplierRepository, table  # noqa: E402
from flowbridge.supplier_assistant.router import create_database_router  # noqa: E402

ORIGIN = "http://127.0.0.1:8001"
PROFILE = ROOT / "langflow/data/local-test.json"
READ_OPERATIONS = frozenset(
    {
        "listProducts",
        "listSuppliers",
        "listSupplierProducts",
        "listPurchaseRequests",
        "getPurchaseRequest",
        "listPurchaseOrders",
        "getPurchaseOrder",
        "getMLPurchaseOrderRisk",
        "getMLPrediction",
    }
)
PREDICT_OPERATIONS = frozenset({"rankMLSuppliers", "predictMLScenario"})
HEALTH_PATHS = frozenset({"/_ops/live", "/_ops/ready", "/_ops/ml/ready"})


@dataclass(frozen=True)
class LocalProfile:
    user_id: UUID
    token: str = field(repr=False)
    ai_user_id: UUID | None = None
    job_context: UUID | None = None

    def __post_init__(self) -> None:
        if len(self.token) < 32 or not self.token.isascii() or not self.token.isprintable():
            raise ValueError("Invalid local test token")

    @classmethod
    def load(cls, path: Path = PROFILE) -> "LocalProfile":
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("version") != 1 or value.get("origin") != ORIGIN:
            raise ValueError("Invalid local test profile")
        return cls(
            UUID(value["user_id"]),
            value["token"],
            UUID(value["ai_user_id"]) if value.get("ai_user_id") else None,
            UUID(value["job_context"]) if value.get("job_context") else None,
        )


def local_settings(*, rag: bool = False) -> Settings:
    return Settings(
        _env_file=ROOT / ".env",
        host="127.0.0.1",
        port=8001,
        docs_enabled=False,
        ml_enabled=True,
        core_identity=None,
        rag_dev_enabled=False,
        rag_postgres_enabled=rag,
        http_access_log_enabled=False,
        trusted_proxy_ips=[],
        cors_origins=[],
    )


def check_settings(settings: Settings) -> None:
    if settings.environment != "development" or settings.database_url is None:
        raise ValueError("Local test requires development environment and configured database")
    if settings.host != "127.0.0.1" or settings.port != 8001:
        raise ValueError("Local test is restricted to 127.0.0.1:8001")
    if settings.core_identity is not None or settings.rag_dev_enabled:
        raise ValueError("Identity core and fixture RAG are excluded from local test")


async def prepare_profile(settings: Settings, path: Path = PROFILE) -> LocalProfile:
    check_settings(settings)
    previous = LocalProfile.load(path) if path.exists() else None
    engine = create_engine(settings, use_null_pool=True)
    try:
        async with engine.connect() as connection:
            await connection.execution_options(postgresql_readonly=True)
            sessions = create_session_factory(engine)
            async with sessions(bind=connection) as session, session.begin():
                users, roles, grants = table("users"), table("roles"), table("user_roles")
                query = (
                    sa.select(users)
                    .join(grants, users.c.user_id == grants.c.user_id)
                    .join(roles, roles.c.role_id == grants.c.role_id)
                    .where(
                        users.c.status == "active",
                        users.c.account_type == "human",
                        roles.c.status == "active",
                        roles.c.role_code.in_(["EMPLOYEE", "APPROVER", "WAREHOUSE"]),
                        grants.c.revoked_at.is_(None),
                    )
                    .distinct()
                    .order_by(users.c.user_id)
                )
                if previous:
                    query = query.where(users.c.user_id == previous.user_id)
                candidates = (await session.execute(query.limit(100))).mappings()
                selected = None
                for candidate in candidates:
                    identity = VerifiedIdentity(
                        candidate["external_issuer"], candidate["external_subject"]
                    )
                    policy = SupplierPolicy(SupplierRepository(session))
                    try:
                        await policy.authenticate(identity)
                        policy.authorize("EMPLOYEE", read=True)
                    except CoreError:
                        continue
                    selected = candidate["user_id"]
                    break
                if selected is None:
                    raise RuntimeError("No active human account with a valid read grant was found")
    finally:
        await engine.dispose()
    profile = previous or LocalProfile(selected, secrets.token_urlsafe(32))
    if not previous:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "origin": ORIGIN,
                    "user_id": str(profile.user_id),
                    "token": profile.token,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return profile


async def read_only_session(request: Request) -> AsyncIterator[AsyncSession]:
    resources = request.app.state.resources
    if resources is None or resources.engine is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    async with resources.engine.connect() as connection:
        await connection.execution_options(postgresql_readonly=True)
        async with AsyncSession(
            bind=connection, expire_on_commit=False, autoflush=False
        ) as session:
            yield session


def route_allowed(route: APIRoute, *, rag: bool = False) -> bool:
    if rag and route.operation_id == "retrieveSupplierKnowledge":
        return route.path == "/internal/v1/tools/retrieve-knowledge" and route.methods == {"POST"}
    if route.path in HEALTH_PATHS:
        return route.methods == {"GET"}
    if route.operation_id in READ_OPERATIONS:
        return route.methods == {"GET"}
    return route.operation_id in PREDICT_OPERATIONS and route.methods == {"POST"}


def rejected(code: str, status: int) -> JSONResponse:
    return JSONResponse(
        {
            "code": code,
            "message": "Local test request rejected.",
            "request_id": str(uuid4()),
            "retryable": False,
        },
        status_code=status,
    )


def create_test_app(settings: Settings, profile: LocalProfile) -> FastAPI:
    check_settings(settings)
    if settings.rag_postgres_enabled and not (profile.ai_user_id and profile.job_context):
        raise ValueError("Prepare a real AI account and JobContext for local PostgreSQL RAG first")
    app = create_app(settings)
    normal_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def read_only_lifespan(application: FastAPI):
        async with normal_lifespan(application):
            # Also covers PostgreSQL RAG's internal session factory, not only get_session.
            application.state.resources.engine.update_execution_options(postgresql_readonly=True)
            if settings.rag_postgres_enabled:
                service = application.state.rag_postgres_service
                # Warm the pinned local model once, before the server reports ready.
                await service.cpu(service.embedder.query, "RAG readiness")
            yield

    app.router.lifespan_context = read_only_lifespan
    # Build the restricted router before inclusion (FastAPI may defer router expansion).
    safe_router = APIRouter()
    candidates = [*operations_router.routes, *create_database_router().routes]
    if settings.ml_enabled:
        candidates.extend(prediction_router.routes)
    safe_router.routes.extend(
        route
        for route in candidates
        if isinstance(route, APIRoute) and route_allowed(route, rag=settings.rag_postgres_enabled)
    )
    # Drop administration, writes, internal ML persistence, indexing and documentation routes.
    app.router.routes.clear()
    app.include_router(safe_router)
    if settings.ml_enabled:
        app.add_api_route("/_ops/ml/ready", model_readiness, include_in_schema=False)
    app.dependency_overrides[get_session] = read_only_session

    async def local_identity(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> VerifiedIdentity:
        rag = request.url.path == "/internal/v1/tools/retrieve-knowledge"
        identifier = profile.ai_user_id if rag else profile.user_id
        account_type = "service" if rag else "human"
        if identifier is None:
            raise CoreError(ErrorCode.FORBIDDEN)
        async with session.begin():
            user = await SupplierRepository(session).get("users", identifier)
            if user["status"] != "active" or user["account_type"] != account_type:
                raise CoreError(ErrorCode.FORBIDDEN)
            return VerifiedIdentity(
                user["external_issuer"], user["external_subject"], account_type=account_type
            )

    app.dependency_overrides[authenticated_identity] = local_identity

    @app.get("/_ops/local-test", include_in_schema=False)
    async def local_status() -> dict:
        return {
            "status": "ready",
            "mode": "langflow-local-test",
            "read_only": True,
            "rag_source": "postgres" if settings.rag_postgres_enabled else "disabled",
        }

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        try:
            local = bool(request.client) and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        if not local:
            return rejected("FORBIDDEN", 403)
        if request.method == "GET" and request.url.path in HEALTH_PATHS:
            return await call_next(request)
        headers = request.headers.getlist("authorization")
        expected = "Bearer " + profile.token
        if len(headers) != 1 or not secrets.compare_digest(headers[0].encode(), expected.encode()):
            return rejected("UNAUTHENTICATED", 401)
        return await call_next(request)

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Local read-only Langflow backend; no SSO")
    parser.add_argument(
        "--prepare", action="store_true", help="Prepare/reuse ignored local profile"
    )
    parser.add_argument(
        "--rag", action="store_true", help="Enable existing PostgreSQL RAG retrieval"
    )
    args = parser.parse_args()
    try:
        previous = LocalProfile.load() if PROFILE.exists() else None
        enabled_rag = args.rag or bool(previous and previous.ai_user_id and previous.job_context)
        settings = local_settings(rag=enabled_rag)
        profile = asyncio.run(prepare_profile(settings))
    except Exception as exc:
        # Do not reflect upstream/validation values that may contain credentials.
        print(
            f"Local test preparation failed ({type(exc).__name__}); check DB and active read user."
        )
        return 1
    if args.prepare:
        print("Local profile ready; existing DB account/grants reused. Database writes: 0.")
        return 0
    print("Langflow local test: " + ORIGIN + " (read-only, local token, no SSO)")
    uvicorn.run(
        create_test_app(settings, profile),
        host="127.0.0.1",
        port=8001,
        access_log=False,
        proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
