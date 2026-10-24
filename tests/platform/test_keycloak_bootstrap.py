"""Targeted checks for additive Keycloak User Profile bootstrap."""

from copy import deepcopy

import pytest
from infra.deployment.keycloak_bootstrap import BootstrapError, bootstrap, merge_profile


def test_merge_profile_preserves_other_fields_and_is_idempotent():
    original = {
        "attributes": [{"name": "email", "permissions": {"edit": ["admin", "user"]}}],
        "unmanagedAttributePolicy": "DISABLED",
    }
    updated, changed = merge_profile(original, "hasura_default_role")
    assert changed
    assert original["attributes"] == [{"name": "email", "permissions": {"edit": ["admin", "user"]}}]
    assert updated["attributes"][0] == original["attributes"][0]
    assert updated["unmanagedAttributePolicy"] == "DISABLED"
    assert merge_profile(updated, "hasura_default_role") == (updated, False)


def test_conflicting_managed_field_is_rejected():
    profile = {
        "attributes": [
            {
                "name": "hasura_default_role",
                "multivalued": False,
                "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]},
            }
        ]
    }
    with pytest.raises(BootstrapError, match="conflicts"):
        merge_profile(profile, "hasura_default_role")


def test_bootstrap_writes_only_missing_field(tmp_path, monkeypatch):
    (tmp_path / "keycloak.env").write_text(
        "KC_BOOTSTRAP_ADMIN_USERNAME=test-admin\nKC_BOOTSTRAP_ADMIN_PASSWORD=test-only\n"
    )
    config = {
        "identity": {"realm": "test-realm", "hasura_default_role_attribute": "my_role"},
        "services": {"keycloak": {"public_url": "http://127.0.0.1:12345"}},
    }
    current = {"attributes": [{"name": "email"}], "groups": [{"name": "custom"}]}
    writes = []

    def fake_request(url, *, method="GET", headers=None, body=None, form=False):
        nonlocal current
        if url.endswith("/token"):
            return {"access_token": "test-token"}
        if method == "PUT":
            writes.append(deepcopy(body))
            current = deepcopy(body)
            return None
        return deepcopy(current)

    monkeypatch.setattr("infra.deployment.keycloak_bootstrap._request", fake_request)
    assert bootstrap(config, tmp_path)
    assert len(writes) == 1
    assert writes[0]["attributes"][0] == {"name": "email"}
    assert writes[0]["groups"] == [{"name": "custom"}]
    assert not bootstrap(config, tmp_path)
    assert len(writes) == 1
