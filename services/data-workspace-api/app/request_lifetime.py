# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep expensive read-only work inside the lifetime of its HTTP caller."""

import asyncio

from fastapi import HTTPException


async def bounded_overview(request, operation, *, timeout=60, poll_interval=0.5):
    async def disconnected():
        while not await request.is_disconnected():
            await asyncio.sleep(poll_interval)

    work = asyncio.create_task(operation)
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait(
            {work, watcher}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if work in done:
            # Preserve source/authorization errors instead of turning them into a timeout.
            return await work
        if watcher in done:
            await watcher
            raise HTTPException(499, "Caller disconnected; warehouse read cancelled")
        raise HTTPException(
            504,
            detail={
                "code": "PLANNING_TIMEOUT",
                "message": "Đọc dữ liệu kho mất quá lâu. Bấm Làm mới dữ liệu để thử lại.",
            },
        )
    finally:
        for task in (work, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, watcher, return_exceptions=True)
