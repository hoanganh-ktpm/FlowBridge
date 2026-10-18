# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# FlowBridge is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
# FlowBridge is distributed without any warranty; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE.

# Application factory and bounded, app-owned runtime resources.

import asyncio
import contextlib
import ssl
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool

from flowbridge.db import create_engine, create_session_factory, probe_connection
from flowbridge.identity.router import install_identity_router
from flowbridge.operations.router import router as operations_router
from flowbridge.settings import Settings
from flowbridge.shared.access_log import access_log_config
from flowbridge.shared.correlation import CorrelationMiddleware
from flowbridge.shared.errors import register_exception_handlers
from flowbridge.shared.openapi import install_openapi
from flowbridge.supplier_assistant.contract import install_database_openapi
from flowbridge.supplier_assistant.router import create_database_router


@dataclass(frozen=True, slots=True)
class Resources:
    # Tài nguyên dùng chung trong một app; session cụ thể vẫn mở riêng theo request.

    http: httpx.AsyncClient
    engine: AsyncEngine | None
    sessions: async_sessionmaker[AsyncSession] | None


def create_app(settings: Settings | None = None) -> FastAPI:
    # Tạo app độc lập; chỉ mở HTTP client và database engine khi vào lifespan.
    configuration = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        if app.state.resources is not None:
            raise RuntimeError("Application lifespan is already running")
        async with AsyncExitStack() as stack:
            # Đăng ký cleanup ngay sau mỗi lần cấp tài nguyên để startup lỗi vẫn đóng được.
            engine = None
            sessions = None
            if configuration.database_url is not None:
                engine = create_engine(configuration)
                stack.push_async_callback(engine.dispose)
                sessions = create_session_factory(engine)
            http = await stack.enter_async_context(
                httpx.AsyncClient(
                    timeout=configuration.http_timeout_seconds,
                    limits=httpx.Limits(
                        max_connections=configuration.http_max_connections,
                        max_keepalive_connections=min(20, configuration.http_max_connections),
                    ),
                    follow_redirects=False,
                    trust_env=False,
                    verify=ssl.create_default_context(cafile=configuration.oidc_ca_bundle)
                    if configuration.oidc_ca_bundle
                    else True,
                )
            )
            if engine is not None:
                try:
                    await probe_connection(engine)
                except Exception:
                    # Upstream errors can contain credentials; expose only a fixed startup error.
                    raise RuntimeError("PostgreSQL startup check failed") from None
            app.state.resources = Resources(http=http, engine=engine, sessions=sessions)
            try:
                if configuration.ml_enabled:
                    from flowbridge.supplier_assistant.prediction.model import TabICLRuntime

                    runtime = TabICLRuntime(configuration)
                    await run_in_threadpool(runtime.load)
                    app.state.ml_runtime = runtime
                if configuration.rag_dev_enabled:
                    from flowbridge.supplier_assistant.knowledge.development import (
                        DevelopmentKnowledgeService,
                    )

                    service = DevelopmentKnowledgeService(configuration)
                    stack.push_async_callback(run_in_threadpool, service.close)
                    app.state.rag_dev_service = service
                if configuration.rag_postgres_enabled:
                    from flowbridge.supplier_assistant.knowledge.embeddings import QwenEmbedder
                    from flowbridge.supplier_assistant.knowledge.postgres_service import (
                        PgKnowledgeService,
                    )
                    from flowbridge.supplier_assistant.knowledge.postgres_store import (
                        PgKnowledgeStore,
                    )

                    pg_store = PgKnowledgeStore(sessions)
                    try:
                        await pg_store.verify_schema()
                    except Exception:
                        raise RuntimeError("PostgreSQL knowledge schema check failed") from None
                    pg_service = PgKnowledgeService(
                        pg_store,
                        QwenEmbedder(configuration.rag_postgres_model_cache_path),
                    )
                    stack.push_async_callback(run_in_threadpool, pg_service.close)
                    app.state.rag_postgres_service = pg_service
                    if configuration.rag_manager_enabled:
                        from flowbridge.supplier_assistant.knowledge.manager import KnowledgeManager

                        manager = KnowledgeManager(
                            sessions, pg_service, max_bytes=configuration.rag_manager_max_bytes
                        )
                        await manager.verify_schema()
                        app.state.knowledge_manager = manager
                        worker = asyncio.create_task(manager.run())

                        async def stop_manager():
                            worker.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await worker

                        stack.push_async_callback(stop_manager)
                yield
            finally:
                # Gỡ tài nguyên khỏi state trước khi ExitStack thực sự đóng client/pool.
                app.state.resources = None
                app.state.rag_dev_service = None
                app.state.ml_runtime = None
                app.state.rag_postgres_service = None
                app.state.knowledge_manager = None

    app = FastAPI(
        title="FlowBridge OpenCore",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if configuration.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if configuration.docs_enabled else None,
    )
    app.state.settings = configuration
    app.state.resources = None
    app.state.rag_dev_service = None
    app.state.ml_runtime = None
    app.state.rag_postgres_service = None
    app.state.knowledge_manager = None
    register_exception_handlers(app)
    if configuration.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=configuration.cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "X-Confirmation-Token",
            ],
            expose_headers=["X-Correlation-Id"],
        )
    # Starlette bọc middleware thêm sau ra ngoài: correlation phải bao cả nhánh lỗi.
    app.add_middleware(CorrelationMiddleware)
    app.include_router(operations_router)
    if configuration.database_url is not None:
        app.include_router(create_database_router())
        install_database_openapi(app)
    if configuration.delivery_statistics_enabled:
        from flowbridge.supplier_assistant.delivery_statistics_router import (
            install_delivery_statistics_router,
        )

        install_delivery_statistics_router(app)
    if configuration.core_identity is not None:
        install_identity_router(app)
    if configuration.ml_enabled:
        from flowbridge.supplier_assistant.prediction.router import install_prediction_router

        install_prediction_router(app)
    if configuration.rag_dev_enabled:
        from flowbridge.supplier_assistant.knowledge.router import install_development_router

        install_development_router(app)
    if configuration.rag_manager_enabled:
        from flowbridge.supplier_assistant.knowledge.manager_router import install_manager_router

        install_manager_router(app)
    install_openapi(app)
    return app


if __name__ == "__main__":
    import uvicorn

    runtime_settings = Settings()
    uvicorn.run(
        create_app(runtime_settings),
        host=runtime_settings.host,
        port=runtime_settings.port,
        log_level=runtime_settings.log_level.lower(),
        access_log=runtime_settings.http_access_log_enabled,
        log_config=access_log_config(),
        proxy_headers=True,
        forwarded_allow_ips=runtime_settings.trusted_proxy_ips,
    )
