"""Targeted C2 wizard and deployment safety checks."""

import socket
import subprocess
import tarfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from scripts import deployment_cli as cli
from scripts.deployment_config import validate
from scripts.verify_source_archive import REQUIRED, verify


class FakeQuestion:
    def __init__(self, answer):
        self.answer = answer

    def ask(self):
        return self.answer


class FakeUI:
    class Choice:
        def __init__(self, title, checked=False):
            self.title = title
            self.checked = checked

    def __init__(self, answers):
        self.answers = iter(answers)
        self.prompts = []

    def _question(self, kind, title, **_):
        self.prompts.append((kind, title))
        return FakeQuestion(next(self.answers))

    def text(self, title, **kwargs):
        return self._question("text", title, **kwargs)

    def select(self, title, **kwargs):
        return self._question("select", title, **kwargs)

    def checkbox(self, title, **kwargs):
        return self._question("checkbox", title, **kwargs)

    def confirm(self, title, **kwargs):
        return self._question("confirm", title, **kwargs)

    def password(self, title, **kwargs):
        return self._question("password", title, **kwargs)


def test_quick_wizard_conditional_questions(monkeypatch):
    ui = FakeUI(["acme", "development", "Quick", ["postgres", "keycloak"], "127.0.0.1"])
    monkeypatch.setattr(cli, "_ui", lambda: ui)
    config = cli.wizard()
    assert config["services"]["hasura"]["enabled"] is False
    assert config["identity"]["issuer"] == "http://127.0.0.1:18443/realms/flowbridge"
    assert not any("database" in title.lower() for _, title in ui.prompts)
    assert config["volumes"]["postgres"] == "acme_postgres"


def test_standalone_default_config_and_non_secret_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert cli._default_config_path() == (
        tmp_path / "FlowBridge" / "deployments" / "default" / "deployment.yaml"
    )
    config_path = tmp_path / "deployment.yaml"
    cli._record_state(config_path, {"name": "test-deployment"}, "running")
    state = (tmp_path / "deployment.state.json").read_text(encoding="utf-8")
    assert "test-deployment" in state
    assert "running" in state
    assert "password" not in state.lower()


def test_new_deployment_rejects_identity_mismatch(monkeypatch, tmp_path):
    path = tmp_path / "deployment.yaml"
    path.write_text(cli.EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(cli, "_ui", lambda: FakeUI(["New", "second"]))
    monkeypatch.setattr(cli, "wizard", lambda *_args, **_kwargs: {"name": "other"})
    assert cli.main(["init", "--config", str(path)]) == 1
    assert not (tmp_path / "deployments" / "second" / "deployment.yaml").exists()


def test_advanced_wizard_database_ports_and_credentials(monkeypatch):
    ports = ["55433", "18444"]
    urls = ["http://127.0.0.1:18444", "http://keycloak:8080"]
    db_names = [f"customer_{key}" for key in cli.DATABASES]
    secrets = {}
    names = cli._env_names(yaml.safe_load(cli.EXAMPLE.read_text(encoding="utf-8")))
    ui = FakeUI(
        [
            "acme",
            "development",
            "Advanced",
            ["postgres", "keycloak"],
            "127.0.0.1",
            *ports,
            *urls,
            *db_names,
            "acme_postgres",
            "acme_keycloak",
            False,
            "acme",
            "Enter manually",
            *(["strong-test-value"] * (len(names) - 2)),
        ]
    )
    monkeypatch.setattr(cli, "_ui", lambda: ui)
    config = cli.wizard(credential_values=secrets)
    assert config["database"]["names"]["core"] == "customer_core"
    assert config["services"]["postgres"]["port"] == 55433
    assert secrets["POSTGRES_PASSWORD"] == "strong-test-value"


def test_wizard_rejects_dependency_and_cancel(monkeypatch):
    monkeypatch.setattr(
        cli, "_ui", lambda: FakeUI(["acme", "development", "Quick", ["hasura"], "127.0.0.1"])
    )
    with pytest.raises(cli.SetupError, match="PostgreSQL"):
        cli.wizard()
    monkeypatch.setattr(cli, "_ui", lambda: FakeUI([None]))
    with pytest.raises(KeyboardInterrupt):
        cli.wizard()


def test_advanced_invalid_port_and_existing_cancel(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli,
        "_ui",
        lambda: FakeUI(
            [
                "acme",
                "development",
                "Advanced",
                ["postgres"],
                "127.0.0.1",
                "invalid",
            ]
        ),
    )
    with pytest.raises(cli.SetupError, match="postgres port"):
        cli.wizard()
    path = tmp_path / "deployment.yaml"
    path.write_text(cli.EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(cli, "_ui", lambda: FakeUI(["Cancel"]))
    assert cli.main(["setup", "--config", str(path)]) == 130


def test_noninteractive_missing_config_and_dry_run(tmp_path, monkeypatch):
    path = tmp_path / "deployment.yaml"
    assert cli.main(["setup", "--non-interactive", "--config", str(path)]) == 1
    config = yaml.safe_load(cli.EXAMPLE.read_text(encoding="utf-8"))
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("\n".join(f"{name}=test-only" for name in cli._env_names(config)))
    monkeypatch.setattr(cli, "_run", lambda *_args, **_kwargs: pytest.fail("Docker called"))
    assert cli.main(["setup", "--non-interactive", "--dry-run", "--config", str(path)]) == 0
    assert not (tmp_path / "infra" / "deployment" / "generated").exists()


def test_init_generates_private_secrets_and_preserves_them(tmp_path, monkeypatch):
    path = tmp_path / "deployment.yaml"
    ui = FakeUI(
        [
            "acme",
            "development",
            "Quick",
            ["postgres", "keycloak", "hasura"],
            "127.0.0.1",
            True,
        ]
    )
    monkeypatch.setattr(cli, "_ui", lambda: ui)
    assert cli.main(["init", "--config", str(path)]) == 0
    secret_path = tmp_path / "secrets" / "deployment.env"
    original = secret_path.read_bytes()
    assert path.exists() and original
    assert "POSTGRES_PASSWORD=" not in path.read_text(encoding="utf-8")
    monkeypatch.setattr(cli, "_ui", lambda: FakeUI(["Use existing"]))
    assert cli.main(["init", "--config", str(path)]) == 0
    assert secret_path.read_bytes() == original


def test_new_dry_run_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "deployment.yaml"
    monkeypatch.setattr(
        cli,
        "_ui",
        lambda: FakeUI(
            [
                "acme",
                "development",
                "Quick",
                ["postgres"],
                "127.0.0.1",
                True,
            ]
        ),
    )
    assert cli.main(["setup", "--dry-run", "--config", str(path)]) == 0
    assert not path.exists()
    assert not (tmp_path / "secrets").exists()


def test_port_conflict_and_missing_secrets():
    config = yaml.safe_load(cli.EXAMPLE.read_text(encoding="utf-8"))
    config["services"]["keycloak"]["port"] = config["services"]["hasura"]["port"]
    errors = validate(config, {})
    assert any("conflicts" in error for error in errors)
    assert any("unset" in error for error in errors)


def test_existing_secrets_preserved_and_setup_reruns(monkeypatch, tmp_path):
    config = yaml.safe_load(cli.EXAMPLE.read_text(encoding="utf-8"))
    env = {name: "test-only" for name in cli._env_names(config)}
    stack = tmp_path / "generated"
    stages = []
    monkeypatch.setattr(cli, "render", lambda *_: stages.append("render"))
    monkeypatch.setattr(cli, "_private_directory", lambda *_: None)
    monkeypatch.setattr(cli, "_run", lambda *_args, **_kwargs: "ok")
    monkeypatch.setattr(cli, "_preflight", lambda *_args: None)
    monkeypatch.setattr(cli, "_compose", lambda *_args: stages.append(_args[2]) or "container")
    monkeypatch.setattr(cli, "_wait_health", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "bootstrap", lambda *_args, **_kwargs: stages.append("bootstrap"))
    monkeypatch.setattr(cli, "check_readiness", lambda *_args: [])
    before = deepcopy(env)
    cli._setup(config, env, stack)
    cli._setup(config, env, stack)
    assert env == before
    assert stages.count("render") == 2
    assert stages.count("bootstrap") == 2


def test_secret_file_is_create_only(tmp_path):
    path = Path(tmp_path) / "secrets" / "deployment.env"
    cli._save_secrets(path, {"TEST_PASSWORD": "private"})
    with pytest.raises(cli.SetupError, match="exists"):
        cli._save_secrets(path, {"TEST_PASSWORD": "changed"})
    assert path.read_text(encoding="utf-8") == "TEST_PASSWORD=private\n"


def test_port_preflight_detects_host_process_and_suggests_replacement(monkeypatch):
    config = yaml.safe_load(cli.EXAMPLE.read_text(encoding="utf-8"))
    for service in ("keycloak", "hasura", "flowable", "core"):
        config["services"][service]["enabled"] = False
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", "")
    )
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        config["services"]["postgres"]["port"] = occupied.getsockname()[1]
        assert cli._port_conflicts(config)[0][2] == "host process or container"
        monkeypatch.setattr(cli, "_ui", lambda: FakeUI(["Suggest free port"]))
        cli._resolve_ports(config)
        assert config["services"]["postgres"]["port"] != occupied.getsockname()[1]


def test_port_owned_by_current_project_is_not_conflict(monkeypatch):
    response = (
        '{"Ports":"127.0.0.1:55432->5432/tcp",'
        '"Labels":"com.docker.compose.project=flowbridge-example",'
        '"Names":"own-postgres"}'
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, response, ""),
    )
    assert cli._port_owner(55432, "flowbridge-example") is None
    assert cli._port_owner(55432, "another-project") == "Docker container own-postgres"


def test_docker_error_categories_redact_credentials(tmp_path, monkeypatch):
    secret = "very-private-password"
    result = subprocess.CompletedProcess([], 1, "", f"port is already allocated; password={secret}")
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: result)
    log = tmp_path / "private" / "setup.log"
    log.parent.mkdir()
    monkeypatch.setattr(cli, "_private_directory", lambda *_args: None)
    with pytest.raises(cli.SetupError, match="port conflict"):
        cli._run(["docker", "compose", "up"], step="services", log_path=log, secret_values=[secret])
    assert secret not in log.read_text(encoding="utf-8")
    assert cli._docker_reason("Cannot connect to the Docker daemon")[0] == "Docker unavailable"


def test_run_decodes_docker_output_as_utf8(monkeypatch):
    captured = {}

    def run(*_args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess([], 0, "ảnh đã tải\n", "")

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli._run(["docker", "compose", "pull"]) == "ảnh đã tải"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_reset_inventories_anonymous_volume_and_requires_exact_confirmation(
    tmp_path, monkeypatch, capsys
):
    stack = tmp_path / "generated"
    stack.mkdir()
    (stack / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    config = {"name": "flowbridge", "volumes": {"postgres": "flowbridge_postgres"}}
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[:3] == ["docker", "ps", "-a"]:
            return "container-one"
        if command[:3] == ["docker", "inspect", "--format"]:
            return (
                '[{"Type":"volume","Name":"anonymous-id","Destination":"/var/lib/postgresql/data"}]'
            )
        if command[:3] == ["docker", "volume", "ls"]:
            return (
                "stale-project-volume"
                if "--filter" in command
                else "flowbridge_postgres\nunrelated"
            )
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(cli, "_run", run)
    monkeypatch.setattr(cli, "_compose", lambda *_args: pytest.fail("destructive call"))
    with pytest.raises(cli.SetupError, match="--confirm-reset flowbridge"):
        cli._reset(config, stack, confirmation="wrong")
    output = capsys.readouterr().out
    assert "anonymous-id [anonymous/unmodeled]" in output
    assert "flowbridge_postgres [declared] -> not currently attached" in output
    assert "stale-project-volume [anonymous/unmodeled] -> not currently attached" in output
    assert "including anonymous volumes" in output


def test_reset_executes_only_after_exact_confirmation(tmp_path, monkeypatch):
    stack = tmp_path / "generated"
    stack.mkdir()
    (stack / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    config = {"name": "flowbridge", "volumes": {}}
    compose_calls = []
    monkeypatch.setattr(cli, "_volume_inventory", lambda _project, _declared: [])
    monkeypatch.setattr(cli, "_compose", lambda *args: compose_calls.append(args))
    cli._reset(config, stack, confirmation="flowbridge")
    assert compose_calls == [(stack, "flowbridge", "down", "--volumes", "--remove-orphans")]


def test_docker_unavailable_does_not_render(tmp_path, monkeypatch):
    config = yaml.safe_load(cli.EXAMPLE.read_text(encoding="utf-8"))
    env = {name: "test-only" for name in cli._env_names(config)}
    monkeypatch.setattr(cli.shutil, "which", lambda *_args: None)
    monkeypatch.setattr(cli, "render", lambda *_args: pytest.fail("render called"))
    with pytest.raises(cli.SetupError, match="Docker unavailable"):
        cli._setup(config, env, tmp_path / "generated")


def test_source_archive_verifier_rejects_secrets(tmp_path):
    path = tmp_path / "source.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name in (*REQUIRED, "secrets/deployment.env"):
            data = b"example"
            member = tarfile.TarInfo("flowbridge-test/" + name)
            member.size = len(data)
            archive.addfile(member, BytesIO(data))
    assert "private directory included" in verify(path)


def test_zip_archive_verifier_rejects_private_file(tmp_path):
    import zipfile

    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED:
            archive.writestr("flowbridge-test/" + name, "example")
        archive.writestr("flowbridge-test\\.tmp\\token.txt", "private")
    assert "private directory included" in verify(path)
