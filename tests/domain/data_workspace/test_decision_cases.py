# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from workspace_app.decision_api import CreateDecision, ReviseDecision
from workspace_app.decision_service import DecisionPolicy, DecisionService


def assignment(org, warehouse, *roles):
    return {"org_id": str(org), "warehouse_ids": [str(warehouse)], "roles": list(roles)}


def test_operational_roster_separates_manager_and_requires_scoped_assignment():
    org, warehouse, coordinator, verifier, logistics, manager = [uuid4() for _ in range(6)]
    ctx = SimpleNamespace(org_id=org, owner_id=coordinator, admin=True)
    bindings = {
        str(coordinator): assignment(org, warehouse, "coordinator", "manager"),
        str(verifier): assignment(org, warehouse, "warehouse", "manager"),
        str(logistics): assignment(org, warehouse, "logistics"),
        str(manager): assignment(org, warehouse, "manager"),
    }
    policy = DecisionPolicy(bindings)
    roster = policy.roster(ctx, warehouse)
    assert [r["principal_id"] for r in roster if r["role"] == "manager"] == [str(manager)]
    assert policy.roles(ctx, uuid4()) == set()
    ctx.org_id = uuid4()
    assert policy.roles(ctx, warehouse) == set()
    with pytest.raises(HTTPException) as denied:
        policy.roster(ctx, warehouse)
    assert denied.value.status_code == 403
    ctx.org_id = org
    del bindings[str(manager)]
    with pytest.raises(HTTPException) as missing:
        DecisionPolicy(bindings).roster(ctx, warehouse)
    assert missing.value.status_code == 503


def test_assignments_can_be_revoked_and_admin_is_not_a_business_role():
    org, warehouse, actor = [uuid4() for _ in range(3)]
    ctx = SimpleNamespace(org_id=org, owner_id=actor, admin=True)
    case = {
        "warehouse_id": str(warehouse),
        "participants": [{"principal_id": str(actor), "role": "manager"}],
    }
    with pytest.raises(HTTPException) as denied:
        DecisionPolicy({}).require_case(ctx, case)
    assert denied.value.status_code == 403
    policy = DecisionPolicy({str(actor): assignment(org, warehouse, "warehouse")})
    with pytest.raises(HTTPException):
        policy.require_case(ctx, case)


def test_create_and_revision_body_cannot_forge_participants_status_or_version():
    body = {
        "simulation_run_id": str(uuid4()),
        "order_id": str(uuid4()),
        "title": "  Đủ hàng trước bàn giao  ",
    }
    assert CreateDecision.model_validate(body).title == "Đủ hàng trước bàn giao"
    for extra in ({"participants": []}, {"status": "APPROVED"}, {"coordinator_id": str(uuid4())}):
        with pytest.raises(ValidationError):
            CreateDecision.model_validate({**body, **extra})
    with pytest.raises(ValidationError):
        CreateDecision.model_validate({**body, "title": "  "})
    for version in (True, 0, "1"):
        with pytest.raises(ValidationError):
            ReviseDecision(simulation_run_id=uuid4(), expected_version=version)


@pytest.mark.asyncio
async def test_malformed_sources_and_missing_sources_fail_closed():
    called = []

    async def authorize(*args):
        called.append(args)

    service = DecisionService(None, None, DecisionPolicy({}), authorize)
    for payload in ({}, {"sources": [{"url": "http://arbitrary/"}]}):
        with pytest.raises(HTTPException) as invalid:
            await service.permitted_payload(None, payload)
        assert invalid.value.status_code == 409
    assert called == []
