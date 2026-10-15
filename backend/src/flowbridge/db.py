# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# FlowBridge is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
# FlowBridge is distributed without any warranty; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE.

# Async PostgreSQL engine and request-scoped sessions without implicit commits.

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from flowbridge.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    # Tạo pool theo Settings; việc tạo engine chưa mở kết nối tới PostgreSQL.
    if settings.database_url is None:
        raise RuntimeError("Database is not configured")
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        hide_parameters=True,
        echo=False,
        connect_args={
            "timeout": settings.database_connect_timeout_seconds,
            "command_timeout": settings.database_command_timeout_seconds,
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # Tạo factory; service quyết định flush/commit và ranh giới transaction.
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def probe_connection(engine: AsyncEngine) -> None:
    # Check connectivity without modifying data; always return the connection to the pool.
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    # One session per request; the service owns commit/transaction boundaries.
    resources = request.app.state.resources
    if resources is None or resources.sessions is None:
        raise RuntimeError("Database session unavailable outside a configured app lifespan")
    # Context manager đóng session cả khi handler lỗi; dependency không tự commit.
    async with resources.sessions() as session:
        yield session
