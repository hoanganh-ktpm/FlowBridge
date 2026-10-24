from contextlib import asynccontextmanager

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.db.engine import wait_for_database


class StartingDatabase:
    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    @asynccontextmanager
    async def connect(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise SQLAlchemyError("private connection details must not escape")
        yield self

    async def execute(self, statement):
        assert str(statement) == "SELECT 1"


@pytest.mark.asyncio
async def test_startup_waits_for_database_and_stops_retrying():
    database = StartingDatabase(2)
    await wait_for_database(database, attempts=3, delay=0)
    assert database.calls == 3


@pytest.mark.asyncio
async def test_readiness_failure_is_bounded_and_sanitized():
    database = StartingDatabase(10)
    with pytest.raises(RuntimeError, match="readiness failed") as error:
        await wait_for_database(database, attempts=2, delay=0)
    assert "private connection" not in str(error.value)
    assert database.calls == 2
