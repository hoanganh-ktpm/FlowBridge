from types import SimpleNamespace

import httpx
import pytest

from app.platform.business import BusinessRuntime
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity


def test_business_permission_requires_explicit_org_and_operation_binding():
    boundary = object.__new__(BusinessRuntime)
    boundary.settings = SimpleNamespace(
        actor_bindings={"staff": {"org_id": "org-a", "permissions": ["stock.reserve"]}}
    )
    actor = VerifiedIdentity("https://local.test", "staff")
    boundary.allow(actor, "org-a", "stock.reserve")
    for org, action in (("org-b", "stock.reserve"), ("org-a", "stock.handoff")):
        with pytest.raises(CoreError) as denied:
            boundary.allow(actor, org, action)
        assert denied.value.code == ErrorCode.FORBIDDEN


def test_business_http_timeout_is_an_error_without_secret_diagnostics():
    boundary = object.__new__(BusinessRuntime)
    boundary.settings = SimpleNamespace(service_bases={"sales": "http://127.0.0.1:18101"})

    def upstream(request):
        raise httpx.ReadTimeout("private token and database details")

    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        boundary.http = http
        with pytest.raises(CoreError) as rejected:
            boundary.remote("sales", "GET", "/api/v1/sales/workflow/orders/test", "private-token")
    assert rejected.value.code == ErrorCode.UPSTREAM_TIMEOUT
    assert "private" not in str(rejected.value)
