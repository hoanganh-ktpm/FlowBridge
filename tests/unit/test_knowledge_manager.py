# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from flowbridge.main import create_app
from flowbridge.settings import Settings
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
