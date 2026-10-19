# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


chat = load_script("chat_stack")
container = load_script("container_chat")


def native_state(directory: Path) -> Path:
    directory.mkdir(parents=True)
    with sqlite3.connect(directory / "langflow.db") as connection:
        connection.execute('CREATE TABLE "user" (id TEXT PRIMARY KEY,username TEXT,is_active INT)')
        connection.execute(
            "CREATE TABLE flow (id TEXT PRIMARY KEY,user_id TEXT,endpoint_name TEXT)"
        )
        connection.execute('INSERT INTO "user" VALUES (?,?,?)', ("owner", "native-owner", 1))
        for role in ("central", "database", "prediction", "knowledge"):
            connection.execute(
                "INSERT INTO flow VALUES (?,?,?)", (role, "owner", "flowbridge-ver2-" + role)
            )
    (directory / "secret_key").write_bytes(b"original-native-encryption-key")
    return directory


def test_snapshot_is_consistent_and_never_overwrites_live_source(tmp_path):
    source = native_state(tmp_path / "source")
    before = (source / "langflow.db").read_bytes()
    destination = tmp_path / "seed"
    chat.snapshot_langflow(source, destination)
    with sqlite3.connect(destination / "langflow.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM flow").fetchone()[0] == 4
    assert (source / "langflow.db").read_bytes() == before
    assert (destination / "secret_key").read_bytes() == (source / "secret_key").read_bytes()
    # Restart uses the first stable snapshot; native edits do not overwrite container state.
    snapshot = (destination / "langflow.db").read_bytes()
    with sqlite3.connect(source / "langflow.db") as connection:
        connection.execute("DELETE FROM flow WHERE id='knowledge'")
    chat.snapshot_langflow(source, destination)
    assert (destination / "langflow.db").read_bytes() == snapshot
    with pytest.raises(chat.StackError, match="another source"):
        chat.snapshot_langflow(tmp_path / "other", destination)


def test_partial_snapshot_and_missing_inputs_fail_without_overwrite(tmp_path):
    source = native_state(tmp_path / "source")
    destination = tmp_path / "seed"
    destination.mkdir()
    (destination / "langflow.db").write_bytes(b"partial")
    with pytest.raises(chat.StackError, match="Partial"):
        chat.snapshot_langflow(source, destination)
    assert (destination / "langflow.db").read_bytes() == b"partial"
    with pytest.raises(chat.StackError, match="required"):
        chat.snapshot_langflow(tmp_path / "absent", tmp_path / "fresh")


def test_seed_owner_reads_wal_backup_without_creating_secret_sidecars(tmp_path):
    source = native_state(tmp_path / "source")
    connection = sqlite3.connect(source / "langflow.db")
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        destination = tmp_path / "seed"
        chat.snapshot_langflow(source, destination)
    finally:
        connection.close()
    before = {p.name: p.read_bytes() for p in destination.iterdir()}
    assert container.seed_owner(destination / "langflow.db") == "native-owner"
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == before


def test_prepare_persists_secrets_and_separates_backend_provider_caller_material(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(container.os, "chown", lambda *_: None, raising=False)
    source = native_state(tmp_path / "source")
    state, private, backend = (tmp_path / name for name in ("state", "private", "backend"))
    agent = tmp_path / "agent.env"
    agent.write_text(
        "OPENAI_API_KEY=provider-key\nFLOWBRIDGE_USER_ACCESS_TOKEN=never-copy-user\n"
        "FLOWBRIDGE_AI_ACCESS_TOKEN=never-copy-ai\nFLOWBRIDGE_JOB_CONTEXT=never-copy-job\n"
        "FLOWBRIDGE_DATABASE_PASSWORD=never-copy-db\n",
        encoding="utf-8",
    )

    def prepare(**overrides):
        container.prepare_chat(
            state,
            private,
            backend,
            source / "langflow.db",
            source / "secret_key",
            agent,
            **overrides,
        )

    prepare()
    runtime = json.loads((private / "runtime.json").read_text("utf-8"))
    api = json.loads((backend / "runtime.json").read_text("utf-8"))
    assert runtime["LANGFLOW_API_KEY_SOURCE"] == "env"
    assert runtime["LANGFLOW_SUPERUSER"] == "native-owner"
    assert runtime["LANGFLOW_AUTO_LOGIN"] == "false"
    assert runtime["LANGFLOW_API_KEY"] == api["FLOWBRIDGE_LANGFLOW_API_KEY"]
    assert runtime["FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET"] == api["FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET"]
    assert runtime["OPENAI_API_KEY"] == "provider-key"
    assert "OPENAI_API_KEY" not in api
    assert len(api["FLOWBRIDGE_AGENT_HUB_CREDENTIAL_KEY"]) >= 32
    assert "FLOWBRIDGE_AGENT_HUB_CREDENTIAL_KEY" not in runtime
    assert api["FLOWBRIDGE_AGENT_HUB_CREDENTIAL_KEY"] != api["FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET"]
    assert "never-copy" not in json.dumps(runtime) + json.dumps(api)
    assert (state / "secret_key").read_bytes() == (source / "secret_key").read_bytes()
    # Key rotation in source env updates provider config, never the persistent bridge/API secrets.
    agent.write_text("OPENAI_API_KEY=updated-provider\n", "utf-8")
    prepare()
    new = json.loads((private / "runtime.json").read_text("utf-8"))
    assert new["OPENAI_API_KEY"] == "updated-provider"
    assert new["LANGFLOW_API_KEY"] == runtime["LANGFLOW_API_KEY"]
    assert json.loads((backend / "runtime.json").read_text("utf-8")) == api
    with pytest.raises(ValueError, match="ports changed"):
        prepare(api_port=18182)
    assert json.loads((backend / "runtime.json").read_text("utf-8")) == api


def test_incomplete_flow_set_or_partial_volume_never_creates_new_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(container.os, "chown", lambda *_: None, raising=False)
    source = native_state(tmp_path / "source")
    with sqlite3.connect(source / "langflow.db") as connection:
        connection.execute("DELETE FROM flow WHERE id='prediction'")
    with pytest.raises(ValueError, match="four ver2"):
        container.seed_owner(source / "langflow.db")
    state = tmp_path / "state"
    state.mkdir()
    (state / "langflow.db").write_bytes(b"existing")
    with pytest.raises(ValueError, match="Partial chat state"):
        container.prepare_chat(state, tmp_path / "private", tmp_path / "backend")
    assert (state / "langflow.db").read_bytes() == b"existing"


def test_overlay_reuses_core_stack_and_down_preserves_all_volumes(tmp_path):
    stack = chat.ChatStack("flowbridge-chat", tmp_path / "private.env", ml=True, rag=True)
    command = stack.command("down")
    assert command.count("-f") == 2
    assert str(ROOT / "infra/dev/compose.yaml") in command
    assert str(ROOT / "infra/chat/compose.yaml") in command
    assert command[-3:] == ["--profile", "*", "down"]
    assert "--volumes" not in command
    assert stack.env["FLOWBRIDGE_CHAT_ML"] == "true"
    assert stack.env["FLOWBRIDGE_CHAT_RAG"] == "true"
    with pytest.raises(chat.StackError):
        chat.ChatStack("flowbridge-chat", tmp_path / "private.env", langflow_port=18082)


def test_compose_has_no_credential_values_and_separates_private_mounts():
    overlay = yaml.safe_load((ROOT / "infra/chat/compose.yaml").read_text("utf-8"))
    services = overlay["services"]
    langflow = services["langflow"]
    assert langflow["network_mode"] == "service:identity"
    assert langflow["read_only"]
    assert "backend-config" not in str(langflow["volumes"])
    assert "chat-config" not in str(langflow["volumes"])
    assert "langflow-config" not in str(services["api"]["volumes"])
    assert not langflow.get("ports")
    assert all(p.startswith("127.0.0.1:") for p in services["identity"]["ports"])
    assert "migrate" in services and services["migrate"]["profiles"] == ["tools"]
    for service in services.values():
        assert "docker.sock" not in str(service)
        assert not any(
            "SECRET" in key or "PASSWORD" in key or "API_KEY" in key
            for key in service.get("environment", {})
        )
    reference = dotenv_values(ROOT / "infra/chat/images.env")["FLOWBRIDGE_LANGFLOW_IMAGE"]
    assert reference.startswith("langflowai/langflow:1.12.6@sha256:")
    assert len(reference.rsplit(":", 1)[1]) == 64


def test_credentials_export_is_captured_and_never_in_command_line(tmp_path, monkeypatch, capsys):
    stack = chat.ChatStack("flowbridge-chat", tmp_path / "private.env")
    stack.directory = tmp_path / "output"
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        Path(args[-1]).write_text(
            json.dumps(
                {
                    "bootstrap_admin": "operator",
                    "bootstrap_password": "test-only-sensitive",
                    "test_users": {},
                    "service_secrets": {"must-not-export": "internal"},
                }
            ),
            "utf-8",
        )
        return SimpleNamespace(stdout="Copied")

    monkeypatch.setattr(stack, "run", run)
    saved = stack.credentials()
    content = json.loads(saved.read_text("utf-8"))
    assert content["keycloak_admin"]["password"] == "test-only-sensitive"
    assert "service_secrets" not in content
    assert calls[0][0][0] == "cp"
    assert calls[0][1] == {"capture": True}
    assert "sensitive" not in str(calls)
    assert "sensitive" not in capsys.readouterr().out


def test_starting_or_unavailable_engine_fails_without_claiming_success(tmp_path, monkeypatch):
    stack = chat.ChatStack("flowbridge-chat", tmp_path / "private.env")
    monkeypatch.setattr(chat.Stack, "check_engine", lambda *args, **kwargs: None)

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 20
        raise chat.subprocess.TimeoutExpired("docker", 20)

    monkeypatch.setattr(chat.subprocess, "run", timeout)
    with pytest.raises(chat.StackError, match="still starting"):
        stack.check_engine()
    monkeypatch.setattr(
        chat.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="")
    )
    with pytest.raises(chat.StackError, match="Linux Engine"):
        stack.check_engine()


def test_model_config_maps_host_paths_and_rejects_unrelated_secrets(tmp_path, monkeypatch):
    source = tmp_path / "models.json"
    source.write_text(
        json.dumps(
            {
                "FLOWBRIDGE_ML_CHECKPOINT_PATH": "C:\\models\\chosen.pt",
                "FLOWBRIDGE_ML_CONTEXT_PATH": "/host/context/chosen.json",
            }
        ),
        "utf-8",
    )
    monkeypatch.setattr(container.os, "environ", {"FLOWBRIDGE_CHAT_ML": "true"})
    container.backend_model_settings(source)
    assert container.os.environ["FLOWBRIDGE_ML_CHECKPOINT_PATH"] == str(Path("/models/chosen.pt"))
    assert container.os.environ["FLOWBRIDGE_ML_CONTEXT_PATH"] == str(
        Path("/ml-context/chosen.json")
    )
    assert container.os.environ["FLOWBRIDGE_ML_ENABLED"] == "true"
    assert container.os.environ["FLOWBRIDGE_RAG_POSTGRES_ENABLED"] == "false"
    source.write_text(json.dumps({"FLOWBRIDGE_USER_ACCESS_TOKEN": "must-not-load"}), "utf-8")
    with pytest.raises(ValueError, match="Invalid model settings"):
        container.backend_model_settings(source)
    assert "FLOWBRIDGE_USER_ACCESS_TOKEN" not in container.os.environ


def test_bootstrap_loads_db_and_forwards_standard_cli_without_automatic_apply(monkeypatch):
    import container_dev

    calls = []
    monkeypatch.setattr(container_dev, "load_database", lambda: calls.append("database"))
    monkeypatch.setattr(container.os, "execv", lambda executable, args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["container_chat.py", "bootstrap", "--subject", "real-uuid"])
    assert container.main() == 0
    assert calls[0] == "database"
    assert calls[1][-2:] == ["--subject", "real-uuid"]
    assert "--apply" not in calls[1]
    assert str(container_dev.ROOT / "scripts/bootstrap_identity.py") in calls[1]


def test_team_accounts_is_explicit_and_forwards_actor_to_container(monkeypatch):
    calls = []
    monkeypatch.setattr(chat.ChatStack, "check_engine", lambda *a, **kw: None)
    monkeypatch.setattr(chat.ChatStack, "run", lambda self, *a, **kw: calls.append(a))
    monkeypatch.setattr(sys, "argv", ["chat_stack.py", "team-accounts", "--granted-by", "admin-id"])
    assert chat.main() == 0
    assert len(calls) == 1
    assert calls[0][-4:] == (
        "scripts/container_chat.py",
        "team-accounts",
        "--granted-by",
        "admin-id",
    )
    assert calls[0][:5] == ("run", "--build", "--rm", "--no-deps", "tools")


def test_team_container_loads_db_and_uses_own_identity_profile(monkeypatch):
    import container_dev

    calls = []
    monkeypatch.setattr(container_dev, "load_database", lambda: calls.append("database"))
    monkeypatch.setattr(container.os, "execv", lambda executable, args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["container_chat.py", "team-accounts"])
    assert container.main() == 0
    assert calls[0] == "database"
    assert calls[1][-3:] == [
        str(container_dev.ROOT / "scripts/team_accounts.py"),
        "--identity-dir",
        str(container_dev.STATE),
    ]
