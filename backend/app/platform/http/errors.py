# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# You may redistribute and modify it under GNU GPL version 3 or later.
# It is provided without any warranty, including MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See LICENSE for the full terms.

# Contract error DTOs and sanitized HTTP exception boundaries.

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.platform.http.correlation import HEADER_NAME, ID_MAX_LENGTH, ID_PATTERN

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INVALID_STATE = "INVALID_STATE"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID = "TOKEN_INVALID"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    status_code: int
    message: str
    retryable: bool = False


ERROR_SPECS: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.UNAUTHENTICATED: ErrorSpec(401, "Authentication is required."),
    ErrorCode.FORBIDDEN: ErrorSpec(403, "Access is denied."),
    ErrorCode.NOT_FOUND: ErrorSpec(404, "Resource not found."),
    ErrorCode.VALIDATION_ERROR: ErrorSpec(422, "Request is invalid."),
    ErrorCode.VERSION_CONFLICT: ErrorSpec(409, "Resource version conflicts."),
    ErrorCode.IDEMPOTENCY_CONFLICT: ErrorSpec(409, "Idempotency key conflicts."),
    ErrorCode.INVALID_STATE: ErrorSpec(409, "Resource state does not allow this operation."),
    ErrorCode.UPSTREAM_UNAVAILABLE: ErrorSpec(503, "Dependency is not ready.", True),
    ErrorCode.UPSTREAM_TIMEOUT: ErrorSpec(504, "Dependency timed out.", True),
    ErrorCode.RATE_LIMITED: ErrorSpec(429, "Request rate limit exceeded.", True),
    ErrorCode.TOKEN_EXPIRED: ErrorSpec(401, "Authentication has expired."),
    ErrorCode.TOKEN_INVALID: ErrorSpec(401, "Authentication is invalid."),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(500, "Internal server error."),
}


class ErrorDetail(BaseModel):
    # Metadata công khai do service chọn; không đưa input hoặc lỗi upstream thô vào.

    model_config = ConfigDict(extra="forbid", frozen=True)
    field: str
    reason: str


class ErrorResponse(BaseModel):
    # Envelope theo hợp đồng API; không chấp nhận trường ngoài schema.

    model_config = ConfigDict(extra="forbid", frozen=True)
    code: ErrorCode
    message: Annotated[str, StringConstraints(min_length=1, max_length=2000, pattern=r"\S")]
    request_id: Annotated[
        str, StringConstraints(min_length=1, max_length=ID_MAX_LENGTH, pattern=ID_PATTERN)
    ]
    retryable: bool
    details: list[ErrorDetail] = Field(default_factory=list)


class CoreError(Exception):
    # Expected service failure; details must contain only public, trusted metadata.

    def __init__(
        self,
        code: ErrorCode,
        *,
        details: tuple[ErrorDetail, ...] = (),
        retry_after: int | None = None,
    ) -> None:
        self.code = code
        self.details = details
        if retry_after is not None and retry_after < 1:
            raise ValueError("retry_after must be positive")
        self.retry_after = retry_after
        super().__init__(ERROR_SPECS[code].message)


def error_response(
    request: Request,
    code: ErrorCode,
    *,
    status_code: int | None = None,
    details: tuple[ErrorDetail, ...] = (),
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    # Build a public envelope without copying exception/input data.
    spec = ERROR_SPECS[code]
    request_id = request.state.correlation_id
    envelope = ErrorResponse(
        code=code,
        message=spec.message,
        request_id=request_id,
        retryable=spec.retryable,
        details=list(details),
    )
    response_headers = dict(headers or {})
    # Body và header luôn dùng cùng ID, không tin header do handler khác cung cấp.
    response_headers[HEADER_NAME] = request_id
    return JSONResponse(
        status_code=status_code if status_code is not None else spec.status_code,
        content=envelope.model_dump(mode="json", exclude={"details"} if not details else set()),
        headers=response_headers,
    )


async def handle_core_error(request: Request, exc: CoreError) -> JSONResponse:
    headers = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    if ERROR_SPECS[exc.code].status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return error_response(request, exc.code, details=exc.details, headers=headers)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    # JSON chưa parse được là 400; dữ liệu đã parse nhưng sai schema là 422.
    malformed = any(error["type"] == "json_invalid" for error in errors)
    # Field paths, error messages, ctx and inputs can all contain caller secrets.
    locations = {"body", "query", "path", "header", "cookie"}
    details = tuple(
        ErrorDetail(
            field=(
                str(error["loc"][0])
                if error.get("loc") and error["loc"][0] in locations
                else "request"
            ),
            reason="Invalid value.",
        )
        for error in errors[:20]
    )
    return error_response(
        request, ErrorCode.VALIDATION_ERROR, status_code=400 if malformed else 422, details=details
    )


async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    codes = {
        401: ErrorCode.UNAUTHENTICATED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.VERSION_CONFLICT,
        429: ErrorCode.RATE_LIMITED,
        503: ErrorCode.UPSTREAM_UNAVAILABLE,
        504: ErrorCode.UPSTREAM_TIMEOUT,
    }
    code = codes.get(
        exc.status_code,
        ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else ErrorCode.VALIDATION_ERROR,
    )
    # Only protocol headers generated by the framework are forwarded, never detail.
    headers = {
        key: value
        for key, value in (exc.headers or {}).items()
        if key.lower() in {"allow", "retry-after", "www-authenticate"}
    }
    if exc.status_code == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return error_response(request, code, status_code=exc.status_code, headers=headers)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Không log exc/traceback: thông báo exception có thể chứa token, DSN hoặc input.
    logger.error("Unhandled request error request_id=%s", request.state.correlation_id)
    return error_response(request, ErrorCode.INTERNAL_ERROR)


class ErrorBoundaryMiddleware:
    # Prevent the server from logging raw exception values, including failed streams.

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def track_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, track_send)
        except Exception as exc:
            # Chặn lỗi gốc trước ServerErrorMiddleware vốn sẽ re-raise tới server logger.
            response = await handle_unexpected_error(Request(scope), exc)
            if started:
                # Không thể đổi status/body sau khi gửi header; hủy stream bằng lỗi an toàn.
                raise RuntimeError("Response failed after headers were sent") from None
            await response(scope, receive, send)


def register_exception_handlers(app: FastAPI) -> None:
    # Install handlers before app startup; correlation middleware must wrap this boundary.
    app.add_exception_handler(CoreError, handle_core_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(HTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
    app.add_middleware(ErrorBoundaryMiddleware)
