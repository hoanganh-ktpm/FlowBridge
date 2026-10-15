# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Fixture test mẫu: chặn engine/HTTP thật, dựng client ASGI sample mode.

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio

from flowbridge import main
from flowbridge.main import create_app
from flowbridge.settings import Settings


@pytest.fixture(autouse=True)
def reject_engine_io(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mọi test mẫu fail ngay nếu code tạo engine hoặc gọi upstream HTTP.
    def forbidden_engine(*args: object, **kwargs: object) -> None:
        pytest.fail("Sample requests must not create a database engine")

    async def forbidden_http(*args: object, **kwargs: object) -> None:
        pytest.fail("Sample requests must not contact an HTTP upstream")

    monkeypatch.setattr(main, "create_engine", forbidden_engine)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_http)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    # Dựng app sample mode test kèm docs rồi phục vụ qua ASGI transport.
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            sample_mode=True,
            docs_enabled=True,
            database_url=None,
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://sample"
        ) as client:
            yield client
