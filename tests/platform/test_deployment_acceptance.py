from pathlib import Path

import pytest
from scripts import deployment_acceptance as acceptance


def test_acceptance_always_removes_business_fixtures(monkeypatch):
    calls = []
    monkeypatch.setattr(
        acceptance, "_configure_fixture_environment", lambda _path: calls.append("configure")
    )
    monkeypatch.setattr(acceptance, "seed_databases", lambda _names: calls.append("seed"))
    monkeypatch.setattr(acceptance, "verify_demo_rows", lambda _names: calls.append("verify"))
    monkeypatch.setattr(
        acceptance,
        "_run_acceptance",
        lambda _path: (_ for _ in ()).throw(RuntimeError("acceptance failed")),
    )
    monkeypatch.setattr(acceptance, "remove_demo_rows", lambda _names: calls.append("clean"))

    with pytest.raises(RuntimeError, match="acceptance failed"):
        acceptance.run(Path("deployment.yaml"))

    assert calls == ["configure", "seed", "verify", "clean"]
