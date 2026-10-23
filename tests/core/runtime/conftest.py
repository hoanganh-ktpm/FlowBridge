import pytest_asyncio

from .fakes import Harness


@pytest_asyncio.fixture
async def harness():
    value = Harness()
    try:
        yield value
    finally:
        await value.close()
