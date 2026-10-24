from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.db.postgres import PostgresCoreTransaction


@pytest.mark.asyncio
@pytest.mark.parametrize("details", [None, {"changed": True}])
async def test_optional_audit_details_bind_sql_null_not_json_null(details):
    connection = AsyncMock()
    connection.scalar.side_effect = ["human", datetime.now(UTC)]
    transaction = PostgresCoreTransaction(connection)
    await transaction.audit(uuid4(), "audit-test", "test.action", uuid4(), details=details)
    statement = connection.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    if details is None:
        assert "diff_json" not in compiled.params
        assert "NULL" in str(compiled)
    else:
        assert compiled.params["diff_json"] == details
