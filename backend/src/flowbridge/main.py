# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# FlowBridge is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
# FlowBridge is distributed without any warranty; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE.

# Application factory and bounded, app-owned runtime resources.

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowbridge.data.adapter import SampleDataAdapter
from flowbridge.data.router import router as data_router
from flowbridge.data.service import DataService
from flowbridge.db import create_engine, create_session_factory, probe_connection
from flowbridge.events.adapter import SampleEventsAdapter
from flowbridge.events.router import router as events_router
from flowbridge.events.service import EventsService
from flowbridge.files.adapter import SampleFilesAdapter
from flowbridge.files.router import router as files_router
from flowbridge.files.service import FilesService
from flowbridge.identity.router import router as identity_router
from flowbridge.identity.service import IdentityService, SampleIdentityAdapter
from flowbridge.operations.router import router as operations_router
from flowbridge.sample import SampleContext
from flowbridge.settings import Settings
from flowbridge.shared.contract import install_sample_openapi
from flowbridge.shared.correlation import CorrelationMiddleware
from flowbridge.shared.errors import register_exception_handlers
from flowbridge.workflow.adapter import SampleWorkflowAdapter
from flowbridge.workflow.router import router as workflow_router
from flowbridge.workflow.service import WorkflowService


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
                yield
            finally:
                # Gỡ tài nguyên khỏi state trước khi ExitStack thực sự đóng client/pool.
                app.state.resources = None

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
    register_exception_handlers(app)
    # Starlette bọc middleware thêm sau ra ngoài: correlation phải bao cả nhánh lỗi.
    app.add_middleware(CorrelationMiddleware)
    app.include_router(operations_router)
    if configuration.sample_mode:
        app.state.sample_context = SampleContext()
        app.state.identity_service = IdentityService(SampleIdentityAdapter())
        app.state.data_service = DataService(SampleDataAdapter())
        app.state.files_service = FilesService(SampleFilesAdapter())
        app.state.workflow_service = WorkflowService(SampleWorkflowAdapter())
        app.state.events_service = EventsService(SampleEventsAdapter())
        for router in (identity_router, data_router, files_router, workflow_router, events_router):
            app.include_router(router)
        install_sample_openapi(app)
    return app


if __name__ == "__main__":
    import uvicorn

    runtime_settings = Settings()
    uvicorn.run(
        create_app(runtime_settings),
        host=runtime_settings.host,
        port=runtime_settings.port,
        log_level=runtime_settings.log_level.lower(),
        access_log=False,
    )
