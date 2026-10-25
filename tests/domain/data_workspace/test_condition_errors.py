# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from workspace_app.decision_service import DecisionService


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["confirmed", "approved"])
async def test_unmet_conditions_explain_the_block_and_never_send_a_workflow_command(outcome):
    actor, revision = uuid4(), uuid4()
    workflow = SimpleNamespace(request=AsyncMock())
    service = DecisionService(None, None, None, None, workflow=workflow)
    service.get = AsyncMock(
        return_value={
            "revision": {"id": str(revision)},
            "workflow_instance_ref": "engine-instance",
            "participants": [{"principal_id": str(actor), "role": "logistics"}],
        }
    )
    service.checked = AsyncMock(
        return_value={
            "warehouse_confirmable": True,
            "logistics_confirmable": False,
            "logistics_reasons": [
                {"code": "BEFORE_PREPARATION_COMPLETE", "message": "Mốc đã chứng minh: 09:30."}
            ],
        }
    )
    with pytest.raises(HTTPException) as failure:
        await service.complete_task(
            SimpleNamespace(owner_id=actor),
            uuid4(),
            uuid4(),
            SimpleNamespace(revision_id=revision, outcome=outcome),
            "same-command",
        )
    assert failure.value.status_code == 409
    assert failure.value.detail["code"] == "DECISION_CONDITIONS_UNMET"
    if outcome == "confirmed":
        assert "09:30" in failure.value.detail["message"]
    workflow.request.assert_not_awaited()
