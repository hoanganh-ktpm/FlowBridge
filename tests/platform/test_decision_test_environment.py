# SPDX-License-Identifier: GPL-3.0-or-later
import pytest
from scripts.decision_test_environment import child_environment, validate_test_url

URL = "postgresql+psycopg://workspace_owner:test@127.0.0.1:55555/flowbridge_workspace_db"


@pytest.mark.parametrize(
    "value",
    [
        URL.replace("127.0.0.1", "aws-0.pooler.supabase.com"),
        URL.replace("flowbridge_workspace_db", "postgres"),
        URL.replace("workspace_owner", "postgres"),
        URL + "?host=external.example",
        URL.replace(":55555", ""),
    ],
)
def test_external_or_wrong_test_target_is_rejected(value):
    with pytest.raises(ValueError):
        validate_test_url(value)


def test_child_cannot_inherit_live_targets_or_opt_in_flags(monkeypatch):
    monkeypatch.setenv("FLOWBRIDGE_DATABASE_URL", "shared-database")
    monkeypatch.setenv("FLOWBRIDGE_ALLOW_SUPABASE_TEST_WRITES", "1")
    monkeypatch.setenv("DXOS_TEST_ADMIN_URL", "shared-database")
    monkeypatch.setenv("DATABASE_URL", "shared-database")
    child = child_environment(URL, "run1")
    assert child["FLOWBRIDGE_WORKSPACE_DATABASE_URL"] == URL
    assert child["FLOWBRIDGE_DECISION_TEST_RUN"] == "run1"
    assert not (
        {
            "DATABASE_URL",
            "DXOS_TEST_ADMIN_URL",
            "FLOWBRIDGE_DATABASE_URL",
            "FLOWBRIDGE_ALLOW_SUPABASE_TEST_WRITES",
        }
        & child.keys()
    )
