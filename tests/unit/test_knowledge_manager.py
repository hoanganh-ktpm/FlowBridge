# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.supplier_assistant.knowledge import manager as manager_module
from flowbridge.supplier_assistant.knowledge.manager import KnowledgeManager
from flowbridge.supplier_assistant.knowledge.manager_models import Create, Edit, filename


@pytest.mark.parametrize(
    "name",
    [
        "../config.env",
        "/file.md",
        "a\\b.md",
        "..",
        "CON.md",
        "C:secret.md",
        "a/b",
        ".hidden.md",
        "a\x00.md",
        "PRN.md",
        "a..md",
    ],
)
def test_rejects_unsafe_names(name):
    with pytest.raises(ValueError):
        filename(name)


def test_unicode_filename_normalization_and_strict_content():
    assert filename("Nhận xét An Phú") == "Nhận xét An Phú.md"
    edit = Edit(
        filename="nhan-xet",
        title="Nhận xét",
        expected_revision=1,
        content_md="* Nội dung\r\n\r\nĐơn đặt hàng",
    )
    assert "\r" not in edit.content_md
    with pytest.raises(ValidationError):
        Edit(**{**edit.model_dump(), "expected_revision": "1"})
    with pytest.raises(ValidationError):
        Edit(**{**edit.model_dump(), "content_md": "\ud800"})
    with pytest.raises(ValidationError):
        Create(
            **edit.model_dump(),
            supplier_id=uuid4(),
            reviewer_user_id=uuid4(),
            report_asof=datetime.now(UTC),
        )


def test_manager_config_and_openapi_are_opt_in_and_namespaced():
    with pytest.raises(ValueError):
        Settings(_env_file=None, rag_manager_enabled=True)
    app = create_app(
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://x:y@localhost/test",
            rag_postgres_enabled=True,
            rag_manager_enabled=True,
        )
    )
    schema = app.openapi()
    assert "/api/admin/knowledge/documents" in schema["paths"]
    assert "/internal/v1/tools/retrieve-knowledge" in schema["paths"]
    assert "KMErrorResponse" in schema["components"]["schemas"]


@pytest.mark.asyncio
async def test_manager_does_not_accept_unauthenticated_browser_calls():
    app = create_app(
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://x:y@localhost/test",
            rag_postgres_enabled=True,
            rag_manager_enabled=True,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        for path in ["/documents", "/options", f"/documents/{uuid4()}", f"/jobs/{uuid4()}"]:
            response = await c.get("/api/admin/knowledge" + path)
            assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [TimeoutError, PoolTimeoutError, ConnectionResetError, RuntimeError]
)
async def test_worker_recovers_from_poll_failure_without_logging_secrets(
    monkeypatch, caplog, failure
):
    manager = KnowledgeManager(None, None)
    manager.run_once = AsyncMock(
        side_effect=[failure("secret-password"), True, asyncio.CancelledError()]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(manager_module.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await manager.run()
    assert manager.run_once.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [2, 0.1]
    assert "stage=worker_retry" in caplog.text
    assert "secret-password" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_heartbeat_does_not_kill_worker_or_hide_shutdown(caplog, cancel):
    manager = KnowledgeManager(None, SimpleNamespace(_gate=asyncio.Lock()))
    manager.claim = AsyncMock(return_value={"job_id": uuid4()})
    manager.heartbeat = AsyncMock(side_effect=TimeoutError("secret-password"))

    async def process(job):
        await asyncio.sleep(0)  # Let the heartbeat task fail before process exits.
        if cancel:
            raise asyncio.CancelledError

    manager.process = process
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await manager.run_once()
    else:
        assert await manager.run_once() is True
    assert not manager.service._gate.locked()
    assert "stage=heartbeat_unavailable" in caplog.text
    assert "secret-password" not in caplog.text
