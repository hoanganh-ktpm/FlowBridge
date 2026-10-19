# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# A versioned bridge stream. Native Langflow remains responsible for graph execution.
import asyncio
import contextlib
import json
from datetime import UTC, datetime

from starlette.responses import StreamingResponse

from flowbridge_agent.caller import CALLER
from flowbridge_agent.frontend_response_api import extract_frontend_response


async def stream_hub(app, caller, scope, native_scope, receive, native_receive, send):
    queue = asyncio.Queue(maxsize=512)

    def emit(event: dict) -> None:
        event = {**event, "occurred_at": datetime.now(UTC).isoformat()}
        queue.put_nowait({"kind": "activity", **event})

    caller.emit = emit

    async def execute():
        token = CALLER.set(caller)
        status, raw = 500, bytearray()

        async def capture(event):
            nonlocal status
            if event["type"] == "http.response.start":
                status = event["status"]
            elif event["type"] == "http.response.body":
                raw.extend(event.get("body", b""))
                if len(raw) > 8_388_608:
                    raise ValueError("OUTPUT_TOO_LARGE")

        try:
            await app(native_scope, native_receive, capture)
            if status != 200 or caller.denied_status:
                raise ValueError("FLOW_EXECUTION_FAILED")
            result = extract_frontend_response(json.loads(raw))
            await queue.put(
                {
                    "kind": "result",
                    "result": result,
                    "resources": {
                        "request_ids": sorted(caller.request_ids),
                        "order_ids": sorted(caller.order_ids),
                        "document_references": caller.sources,
                    },
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await queue.put({"kind": "error", "code": "LANGFLOW_EXECUTION_FAILED"})
        finally:
            CALLER.reset(token)
            await queue.put(None)

    async def chunks():
        task = asyncio.create_task(execute())
        try:
            while True:
                try:
                    value = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield b"\n"
                    continue
                if value is None:
                    break
                yield json.dumps(value, ensure_ascii=False, allow_nan=False).encode() + b"\n"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    await StreamingResponse(
        chunks(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )(scope, receive, send)
