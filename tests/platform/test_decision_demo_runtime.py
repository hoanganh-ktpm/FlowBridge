# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest
from scripts import decision_demo_runtime as runtime
from scripts.integration_utils import write_env


def test_demo_process_uses_only_its_private_profile_and_service_module(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "DIRECTORY", tmp_path)
    write_env(
        tmp_path / "workspace.env", {"FLOWBRIDGE_WORKSPACE_CORE_URL": "http://127.0.0.1:18000"}
    )
    for key in ("FLOWBRIDGE_DATABASE_URL", "DXOS_ADMIN_URL", "DATABASE_URL", "PGPASSWORD"):
        monkeypatch.setenv(key, "must-not-inherit")
    values = runtime.process_environment("workspace")
    assert values["FLOWBRIDGE_WORKSPACE_CORE_URL"] == "http://127.0.0.1:18000"
    assert not any(value == "must-not-inherit" for value in values.values())
    assert values["PYTHONPATH"].split(runtime.os.pathsep)[0].endswith("data-workspace-api")


def test_stop_refuses_mismatched_resource_ownership_before_process_operations(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(runtime, "DIRECTORY", tmp_path)
    monkeypatch.setattr(runtime, "state", lambda: {"org_id": "our-org"})
    (tmp_path / "processes.json").write_text(
        json.dumps(
            {
                "org_id": "foreign-org",
                "run_id": "abc",
                "status": "RUNNING",
                "processes": {},
            }
        )
    )
    with pytest.raises(ValueError, match="ownership"):
        runtime.stop()


def test_demo_workspace_loads_only_model_settings_and_preserves_private_overrides(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "DIRECTORY", tmp_path / "demo")
    runtime.DIRECTORY.mkdir()
    (tmp_path / ".tmp/data-workspace").mkdir(parents=True)
    write_env(
        runtime.DIRECTORY / "workspace.env",
        {"FLOWBRIDGE_WORKSPACE_CHRONOS_REVISION": "private-revision"},
    )
    write_env(
        tmp_path / ".tmp/data-workspace/chronos.env",
        {
            "FLOWBRIDGE_WORKSPACE_CHRONOS_PATH": "local-model",
            "FLOWBRIDGE_WORKSPACE_CHRONOS_REVISION": "default-revision",
            "FLOWBRIDGE_WORKSPACE_CORE_URL": "must-not-override-core",
        },
    )
    values = runtime.process_environment("workspace")
    assert values["FLOWBRIDGE_WORKSPACE_CHRONOS_PATH"] == "local-model"
    assert values["FLOWBRIDGE_WORKSPACE_CHRONOS_REVISION"] == "private-revision"
    assert "FLOWBRIDGE_WORKSPACE_CORE_URL" not in values
    write_env(runtime.DIRECTORY / "sales.env", {})
    assert "FLOWBRIDGE_WORKSPACE_CHRONOS_PATH" not in runtime.process_environment("sales")


def test_runtime_rejects_hosted_owner_database_before_reading_local_credentials(monkeypatch):
    from scripts.database import config, local
    from sqlalchemy.engine import make_url

    monkeypatch.setattr(local, "load_local", lambda: None)
    monkeypatch.setattr(
        config,
        "database_url",
        lambda _: make_url(
            "postgresql+psycopg://core_owner:unused@hosted.example:5432/dxos_core_db"
        ),
    )
    with pytest.raises(ValueError, match="guarded local"):
        runtime.local_configuration()
