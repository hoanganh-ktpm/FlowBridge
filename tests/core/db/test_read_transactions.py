from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.db.postgres import PostgresCoreTransaction
from app.platform.http.errors import CoreError, ErrorCode


class CapturedQuery:
    async def execute(self, query):
        self.statement = str(query.compile(dialect=postgresql.dialect()))
        return self

    def mappings(self):
        return self

    def one_or_none(self):
        return None

    def all(self):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("operation", ["get", "find"])
async def test_snapshot_reads_do_not_lock_worker_rows_but_commands_still_do(read_only, operation):
    connection = CapturedQuery()
    transaction = PostgresCoreTransaction(connection, read_only=read_only)
    if operation == "get":
        with pytest.raises(CoreError) as error:
            await transaction.get("job", uuid4())
        assert error.value.code == ErrorCode.NOT_FOUND
    else:
        assert await transaction.find("job", status="RUNNING") == []
    assert ("FOR UPDATE" in connection.statement) == (not read_only)
