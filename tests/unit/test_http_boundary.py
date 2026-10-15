# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# You may redistribute and modify it under GNU GPL version 3 or later.
# It is provided without any warranty, including MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See LICENSE for the full terms.

# HTTP error contracts, secret handling and concurrent correlation isolation.

import asyncio
import os
import re
import traceback
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException, Request
from pydantic import BaseModel, field_validator
from starlette.responses import StreamingResponse

from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.shared.correlation import get_correlation_id
from flowbridge.shared.errors import CoreError, ErrorCode


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in os.environ:
        if name.startswith("FLOWBRIDGE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        (ErrorCode.UNAUTHENTICATED, 401, False),
        (ErrorCode.FORBIDDEN, 403, False),
        (ErrorCode.NOT_FOUND, 404, False),
        (ErrorCode.VALIDATION_ERROR, 422, False),
        (ErrorCode.IDEMPOTENCY_CONFLICT, 409, False),
        (ErrorCode.UPSTREAM_UNAVAILABLE, 503, True),
        (ErrorCode.UPSTREAM_TIMEOUT, 504, True),
        (ErrorCode.RATE_LIMITED, 429, True),
    ],
)
async def test_service_errors_use_contract_envelope(
    code: ErrorCode, status: int, retryable: bool
) -> None:
    app = create_app(Settings())

    @app.get("/_test/error")
    async def fail() -> None:
        raise CoreError(code, retry_after=2 if retryable else None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/_test/error", headers={"X-Correlation-Id": "REQ-001"})
    body = response.json()
    assert response.status_code == status
    assert body == {
        "code": code.value,
        "message": body["message"],
        "request_id": "REQ-001",
        "retryable": retryable,
    }
    assert body["message"].strip()
    assert response.headers["x-correlation-id"] == body["request_id"]
    assert response.headers["content-type"] == "application/json"
    if retryable:
        assert response.headers["retry-after"] == "2"
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 409, 422, 429, 500, 503, 504])
async def test_framework_http_errors_keep_status_and_protocol_headers_only(status: int) -> None:
    app = create_app(Settings())

    @app.get("/_test/error")
    async def fail() -> None:
        raise HTTPException(
            status,
            detail={"password": "secret-value"},
            headers={"Retry-After": "3", "X-Debug": "secret-value"},
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/_test/error")
        missing = await client.get("/does-not-exist")
        wrong_method = await client.post("/_ops/live")
    assert response.status_code == status
    assert "secret-value" not in response.text
    assert "x-debug" not in response.headers
    assert response.headers["retry-after"] == "3"
    assert response.json()["request_id"] == response.headers["x-correlation-id"]
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"
    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "GET"
    assert wrong_method.json()["code"] == "VALIDATION_ERROR"


class SecretInput(BaseModel):
    password: int

    @field_validator("password")
    @classmethod
    def reject_value(cls, value: int) -> int:
        raise ValueError("validator-secret-value")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b'{"password": "input-secret-value"}', 422),
        (b'{"password": 10}', 422),
        (b"{}", 422),
        (b'{"password":', 400),
    ],
)
async def test_validation_does_not_echo_input_or_validator_context(
    body: bytes, status: int
) -> None:
    app = create_app(Settings())

    @app.post("/_test/validate")
    async def validate(payload: SecretInput) -> dict[str, int]:
        return {"value": payload.password}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/_test/validate", content=body, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == status
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["details"] == [{"field": "body", "reason": "Invalid value."}]
    assert "secret-value" not in response.text
    assert "password" not in response.text


@pytest.mark.asyncio
async def test_unexpected_and_response_validation_failures_are_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(Settings())

    @app.get("/_test/crash")
    async def crash() -> None:
        raise RuntimeError("upstream-secret-value")

    @app.get("/_test/invalid-output", response_model=SecretInput)
    async def invalid_output() -> dict[str, str]:
        return {"password": "output-secret-value"}

    # Default raise_app_exceptions=True also proves raw failures do not escape to the server.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/_test/crash", "/_test/invalid-output"):
            response = await client.get(path, headers={"X-Correlation-Id": "REQ-FAIL"})
            assert response.status_code == 500
            assert response.json() == {
                "code": "INTERNAL_ERROR",
                "message": "Internal server error.",
                "request_id": "REQ-FAIL",
                "retryable": False,
            }
            assert response.headers["x-correlation-id"] == "REQ-FAIL"
    assert "request_id=REQ-FAIL" in caplog.text
    assert "secret-value" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert get_correlation_id() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values", [[], [""], ["bad id"], ["a" * 129], ["a\nb"], ["é"], ["A", "B"], ["trailing\n"]]
)
async def test_missing_invalid_or_duplicate_correlation_is_replaced(values: list[str]) -> None:
    app = create_app(Settings())

    @app.get("/_test/correlation")
    async def echo(request: Request) -> dict[str, str | None]:
        return {"context": get_correlation_id(), "header": request.headers["x-correlation-id"]}

    headers = [(b"x-correlation-id", value.encode("latin-1")) for value in values]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/_test/correlation", headers=headers)
    request_id = response.headers["x-correlation-id"]
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", request_id)
    assert request_id not in values
    assert response.json() == {"context": request_id, "header": request_id}
    assert get_correlation_id() is None


@pytest.mark.asyncio
async def test_concurrent_requests_keep_their_own_id_and_reset_context() -> None:
    app = create_app(Settings())
    arrived = 0
    both_arrived = asyncio.Event()

    @app.get("/_test/context")
    async def context(request: Request) -> dict[str, str | None]:
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=2)
        return {"context": get_correlation_id(), "state": request.state.correlation_id}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        responses = await asyncio.gather(
            *(
                client.get("/_test/context", headers={"X-Correlation-Id": request_id})
                for request_id in ("REQ-A", "b" * 128)
            )
        )
    for response, request_id in zip(responses, ("REQ-A", "b" * 128), strict=True):
        assert response.json() == {"context": request_id, "state": request_id}
        assert response.headers["x-correlation-id"] == request_id
    assert get_correlation_id() is None


@pytest.mark.asyncio
async def test_failed_stream_is_aborted_without_raw_exception_or_second_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(Settings())

    async def stream() -> AsyncIterator[bytes]:
        yield b"first chunk"
        raise RuntimeError("stream-secret-value")

    @app.get("/_test/stream")
    async def stream_response() -> StreamingResponse:
        return StreamingResponse(stream())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        with pytest.raises(RuntimeError, match="Response failed after headers were sent") as error:
            await client.get("/_test/stream")
    assert "stream-secret-value" not in "".join(traceback.format_exception(error.value))
    assert "stream-secret-value" not in caplog.text
    assert get_correlation_id() is None
