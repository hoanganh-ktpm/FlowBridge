# SPDX-License-Identifier: GPL-3.0-or-later
"""Local auth watcher recovery; no Keycloak, credentials or model requests."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "local_identity_session", Path(__file__).resolve().parents[1] / "local_identity_session.py"
)
sessions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sessions)


def status_error(status):
    return httpx.HTTPStatusError(
        "fixture-private-response",
        request=httpx.Request("POST", "https://localhost/token"),
        response=httpx.Response(status),
    )


@pytest.mark.parametrize("status", [400, 401, 429, 503])
def test_watch_reauthenticates_after_session_or_upstream_failure(monkeypatch, capsys, status):
    configure = Mock(side_effect=[status_error(status), None])
    sleep = Mock()
    monkeypatch.setattr(sessions, "configure", configure)
    monkeypatch.setattr(sessions.time, "sleep", sleep)

    sessions.maintain_session(Path("isolated-profile"), watch=True)

    assert configure.call_count == 2
    assert all(call.args == (Path("isolated-profile"), True) for call in configure.call_args_list)
    sleep.assert_called_once_with(2)
    output = capsys.readouterr().out
    assert "retrying PKCE login" in output
    assert "fixture-private-response" not in output


def test_watch_backs_off_when_backend_is_restarting(monkeypatch):
    configure = Mock(side_effect=[httpx.ConnectError("private-endpoint")] * 6 + [None])
    sleep = Mock()
    monkeypatch.setattr(sessions, "configure", configure)
    monkeypatch.setattr(sessions.time, "sleep", sleep)

    sessions.maintain_session(Path("isolated-profile"), watch=True)

    assert [call.args[0] for call in sleep.call_args_list] == [2, 4, 8, 16, 30, 30]


def test_one_shot_keeps_http_failure_visible(monkeypatch):
    monkeypatch.setattr(sessions, "configure", Mock(side_effect=status_error(401)))
    sleep = Mock()
    monkeypatch.setattr(sessions.time, "sleep", sleep)
    with pytest.raises(httpx.HTTPStatusError):
        sessions.maintain_session(Path("isolated-profile"), watch=False)
    sleep.assert_not_called()


def test_watch_does_not_hide_invalid_profile_or_shutdown(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr(sessions.time, "sleep", sleep)
    for failure in (ValueError("ISOLATED_LOCAL_IDENTITY_REQUIRED"), KeyboardInterrupt()):
        monkeypatch.setattr(sessions, "configure", Mock(side_effect=failure))
        with pytest.raises(type(failure)):
            sessions.maintain_session(Path("isolated-profile"), watch=True)
    sleep.assert_not_called()
