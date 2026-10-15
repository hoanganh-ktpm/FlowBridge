# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# You may redistribute and modify it under GNU GPL version 3 or later.
# It is provided without any warranty, including MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See LICENSE for the full terms.

# Request-local correlation, following the contract's Id constraints.

import re
from contextvars import ContextVar
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
ID_MAX_LENGTH = 128
HEADER_NAME = "X-Correlation-Id"
_correlation_id: ContextVar[str | None] = ContextVar("flowbridge_correlation_id", default=None)


def get_correlation_id() -> str | None:
    # Return the current request ID; callers outside a request get None.
    return _correlation_id.get()


def normalize_correlation_id(values: list[bytes]) -> str:
    # Accept one valid header or generate an ID without reflecting unsafe input.
    # Không gộp header trùng: các proxy có thể hiểu các giá trị khác nhau.
    if len(values) == 1:
        value = values[0].decode("latin-1")
        if len(value) <= ID_MAX_LENGTH and re.fullmatch(ID_PATTERN, value):
            return value
    return uuid4().hex


class CorrelationMiddleware:
    # Propagate one normalized ID without BaseHTTPMiddleware's context boundary.

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        values = [
            value for name, value in scope.get("headers", []) if name.lower() == b"x-correlation-id"
        ]
        request_id = normalize_correlation_id(values)
        # Mọi lớp phía trong đọc cùng ID đã chuẩn hóa, kể cả khi đầu vào bị thay thế.
        scope["headers"] = [
            (name, value)
            for name, value in scope.get("headers", [])
            if name.lower() != b"x-correlation-id"
        ] + [(b"x-correlation-id", request_id.encode("ascii"))]
        scope.setdefault("state", {})["correlation_id"] = request_id
        token = _correlation_id.set(request_id)

        async def send_with_correlation(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[HEADER_NAME] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_correlation)
        finally:
            # Reset bằng token để phục hồi context cũ cả khi lỗi hoặc request bị hủy.
            _correlation_id.reset(token)
