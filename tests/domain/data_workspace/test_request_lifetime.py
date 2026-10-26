# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio

import pytest
from fastapi import HTTPException
from workspace_app.request_lifetime import bounded_overview


class Caller:
    gone = False

    async def is_disconnected(self):
        return self.gone


@pytest.mark.asyncio
async def test_success_and_permission_denial_keep_their_original_result():
    async def value():
        return {"warehouse_count": 2}

    assert await bounded_overview(Caller(), value()) == {"warehouse_count": 2}

    async def denied():
        raise HTTPException(403, "Grant revoked")

    with pytest.raises(HTTPException) as error:
        await bounded_overview(Caller(), denied())
    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [True, False])
async def test_disconnection_or_deadline_cancels_upstream_work(disconnect):
    caller, cleaned = Caller(), asyncio.Event()
    caller.gone = disconnect

    async def read():
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    with pytest.raises(HTTPException) as error:
        await bounded_overview(caller, read(), timeout=0.02, poll_interval=0.001)
    assert error.value.status_code == (499 if disconnect else 504)
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_parent_cancellation_cleans_up_the_entire_read():
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def read():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(bounded_overview(Caller(), read()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
