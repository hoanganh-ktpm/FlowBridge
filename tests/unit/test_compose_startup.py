# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import compose_startup as startup  # noqa: E402


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False), ("TRUE", True)])
def test_flags_are_explicit_booleans(monkeypatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("TEST_STARTUP_FLAG", value)
    assert startup.enabled("TEST_STARTUP_FLAG") is expected


def test_invalid_flag_fails_instead_of_silently_enabling_writes(monkeypatch) -> None:
    monkeypatch.setenv("TEST_STARTUP_FLAG", "yes")
    with pytest.raises(ValueError, match="true or false"):
        startup.enabled("TEST_STARTUP_FLAG")


def test_missing_snapshot_fails_before_creating_identity_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(startup, "LANGFLOW_CONFIG", tmp_path / "private")
    mutations = []
    monkeypatch.setattr(startup, "prepare_state", lambda: mutations.append("identity"))
    with pytest.raises(Exception, match="required"):
        startup.prepare(tmp_path / "missing", tmp_path / "snapshot")
    assert mutations == []


def test_existing_volume_never_reimports_native_flow_or_key(tmp_path, monkeypatch) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "profile.json").write_text("{}", "utf-8")
    monkeypatch.setattr(startup, "LANGFLOW_CONFIG", private)
    monkeypatch.setattr(startup, "CONFIG", tmp_path / "backend")
    calls = []
    monkeypatch.setattr(startup, "snapshot_langflow", lambda *_: pytest.fail("Unexpected reimport"))
    monkeypatch.setattr(startup, "prepare_state", lambda: calls.append("identity"))
    monkeypatch.setattr(startup, "prepare_chat", lambda **kwargs: calls.append(kwargs))
    import dotenv

    monkeypatch.setattr(
        dotenv,
        "dotenv_values",
        lambda *a, **kw: {
            "FLOWBRIDGE_ML_DEVICE": "cpu",
            "FLOWBRIDGE_DATABASE_PASSWORD": "must-stay-private",
        },
    )
    startup.prepare(tmp_path / "missing-source", tmp_path / "snapshot")
    assert calls[0] == "identity"
    assert calls[1]["seed"] == tmp_path / "snapshot/langflow.db"
    content = (tmp_path / "backend/chat-models.json").read_text("utf-8")
    assert "cpu" in content and "must-stay-private" not in content


@pytest.mark.asyncio
async def test_migrations_disabled_still_require_current_schema(monkeypatch) -> None:
    monkeypatch.setenv("FLOWBRIDGE_COMPOSE_MIGRATE", "false")
    monkeypatch.setattr(startup, "load_database", lambda: None)
    monkeypatch.setattr(startup.subprocess, "run", lambda *a, **kw: pytest.fail("Migration ran"))
    check = AsyncMock()
    monkeypatch.setattr(startup, "check_database", check)
    await startup.initialize_database()
    check.assert_awaited_once_with(require_head=True)


@pytest.mark.asyncio
async def test_accounts_disabled_never_open_db_or_change_grants(monkeypatch) -> None:
    monkeypatch.setenv("FLOWBRIDGE_TEAM_ACCOUNTS_ENABLED", "false")
    monkeypatch.setattr(startup, "load_database", lambda: pytest.fail("DB setup ran"))
    await startup.setup_accounts()


@pytest.mark.asyncio
async def test_configured_actor_is_forwarded_to_existing_account_policy(monkeypatch) -> None:
    from uuid import uuid4

    import team_accounts

    actor = uuid4()
    monkeypatch.setenv("FLOWBRIDGE_TEAM_ACCOUNTS_ENABLED", "true")
    monkeypatch.setenv("FLOWBRIDGE_TEAM_GRANTED_BY", str(actor))
    monkeypatch.setattr(startup, "load_database", lambda: None)
    setup = AsyncMock(return_value={"users": {}})
    monkeypatch.setattr(team_accounts, "setup", setup)
    await startup.setup_accounts()
    args = setup.call_args.args[0]
    assert args.granted_by == actor and args.identity_dir == startup.STATE


def test_failure_exits_without_dumping_connection_secrets(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["compose_startup.py", "prepare"])

    def failure() -> None:
        raise RuntimeError("private-database-password")

    monkeypatch.setattr(startup, "prepare", failure)
    assert startup.main() == 1
    output = capsys.readouterr().err
    assert "failed" in output and "private-database-password" not in output
