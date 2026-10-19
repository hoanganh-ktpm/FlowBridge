# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.identity.bootstrap import bootstrap_identity
from flowbridge.identity.directory import DirectoryIdentity
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.policy import SupplierPolicy


@pytest.mark.asyncio
async def test_procurement_reader_can_read_all_requests_but_cannot_create_or_approve():
    user, role_id = uuid4(), uuid4()
    rows = {
        "users": [{"user_id": user, "status": "active", "account_type": "human"}],
        "roles": [
            {
                "role_id": role_id,
                "role_code": "PROCUREMENT_VIEWER",
                "status": "active",
                "scope_type": "global",
                "allowed_account_type": "human",
            }
        ],
        "user_roles": [{"role_id": role_id, "scope_type": "global", "department_id": None}],
    }
    policy = SupplierPolicy(SimpleNamespace(rows=AsyncMock(side_effect=lambda n, *a, **k: rows[n])))
    await policy.authenticate(VerifiedIdentity("https://issuer.invalid", "subject"))
    policy.authorize("EMPLOYEE", read=True)
    assert str(policy.request_conditions()[0]) == "true"
    policy.authorize_profile()
    for code in ("EMPLOYEE", "APPROVER", "WAREHOUSE", "ADMIN", "AI_SERVICE", "PO_SERVICE"):
        with pytest.raises(CoreError) as error:
            policy.authorize(code)
        assert error.value.code == ErrorCode.FORBIDDEN


@pytest.mark.asyncio
async def test_employee_with_reader_role_still_only_writes_own_requests():
    policy = SupplierPolicy(None)
    policy.user = {"user_id": uuid4(), "account_type": "human"}
    policy.grants = {"EMPLOYEE": [{}], "PROCUREMENT_VIEWER": [{}]}
    policy.authorize("EMPLOYEE", read=True)
    assert str(policy.request_conditions()[0]) == "true"
    policy.authorize("EMPLOYEE")
    assert "requested_by" in str(policy.request_conditions()[0])
    assert "true" not in str(policy.request_conditions()[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [True, False])
async def test_only_active_admin_can_prepare_reader_role_and_grant(monkeypatch, authorized):
    actor, user = uuid4(), uuid4()
    repo = SimpleNamespace(
        active_admins=AsyncMock(return_value=[actor] if authorized else []),
        ensure_procurement_reader_role=AsyncMock(),
    )
    accounts = SimpleNamespace(
        link=AsyncMock(return_value=({"user_id": user}, False)),
        grant=AsyncMock(return_value=({}, True)),
    )
    monkeypatch.setattr("flowbridge.identity.bootstrap.IdentityAccounts", lambda _: accounts)
    audit = SimpleNamespace(bootstrap_applied=AsyncMock())
    identity = DirectoryIdentity("https://issuer.invalid", "subject", "human", "Test", None)
    if not authorized:
        with pytest.raises(CoreError) as error:
            await bootstrap_identity(repo, identity, roles=["PROCUREMENT_VIEWER"], granted_by=actor)
        assert error.value.code == ErrorCode.FORBIDDEN
        repo.ensure_procurement_reader_role.assert_not_awaited()
        accounts.grant.assert_not_awaited()
        return
    result = await bootstrap_identity(
        repo, identity, roles=["PROCUREMENT_VIEWER"], granted_by=actor, audit=audit
    )
    assert result["grants_added"] == 1
    repo.ensure_procurement_reader_role.assert_awaited_once()
    accounts.grant.assert_awaited_once()
    audit.bootstrap_applied.assert_awaited_once_with(user, actor, ["PROCUREMENT_VIEWER"])
