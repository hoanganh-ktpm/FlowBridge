import json

from starlette.datastructures import MutableHeaders
from starlette.requests import Request

from .errors import ErrorCode, error_response


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate object key")
        result[key] = value
    return result


class PrivateJsonMiddleware:
    def __init__(self, app, max_body_bytes=1024 * 1024):
        self.app, self.max_body_bytes = app, max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def private_send(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        if scope["method"] in {"POST", "PATCH", "PUT"}:
            request = Request(scope)
            chunks, size = [], 0
            try:
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > self.max_body_bytes:
                        raise ValueError("Body limit")
                    chunks.append(chunk)
                    if not message.get("more_body", False):
                        break
                body = b"".join(chunks)
                if body:
                    if (
                        request.headers.get("content-type", "").split(";")[0].strip()
                        != "application/json"
                    ):
                        raise ValueError("JSON required")
                    json.loads(
                        body,
                        object_pairs_hook=unique_object,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError()),
                    )
            except (ValueError, UnicodeError, RecursionError):
                await error_response(request, ErrorCode.VALIDATION_ERROR, status_code=400)(
                    scope, receive, private_send
                )
                return

            async def replay():
                return {"type": "http.request", "body": body, "more_body": False}

            await self.app(scope, replay, private_send)
        else:
            await self.app(scope, receive, private_send)
