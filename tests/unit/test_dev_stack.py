# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import importlib.util
import json
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dev = load_script("dev_stack")
container = load_script("container_dev")


@pytest.mark.parametrize(
    "project,api,iam",
    [
        ("other-project", 18082, 19444),
        ("flowbridge-X", 18082, 19444),
        ("flowbridge-test", 8761, 19444),
        ("flowbridge-test", 18082, 18082),
        ("flowbridge-test", 80, 19444),
    ],
)
def test_invalid_namespace_or_ports_rejected_before_running_commands(
    project: str, api: int, iam: int
) -> None:
    with pytest.raises(dev.StackError):
        dev.Stack(project, ROOT / ".env.example", api, iam)


def test_compose_never_builds_with_secrets_or_runs_schema_changes() -> None:
    document = yaml.safe_load((ROOT / "compose.yaml").read_text("utf-8"))
    services = document["services"]
    for name, service in services.items():
        if name != "prepare":
            assert not service.get("secrets"), name
        mounts = str(service.get("volumes", []))
        if name in {"identity", "browser", "client-a", "client-b"}:
            assert "backend-config" not in mounts
        assert "docker.sock" not in mounts
        assert "privileged" not in service
        assert "alembic upgrade" not in str(service)
        assert "seed.py" not in str(service)
        assert not service.get("build", {}).get("secrets")
    assert all(port.startswith("127.0.0.1:") for port in services["identity"]["ports"])
    for name in ("api", "tools", "browser", "client-a", "client-b"):
        assert services[name]["network_mode"] == "service:identity"
        assert not services[name].get("ports")
    assert services["api"]["depends_on"]["identity"]["condition"] == "service_healthy"


def test_down_includes_optional_clients_and_preserves_volumes_by_default() -> None:
    stack = dev.Stack("flowbridge-dev", ROOT / ".env.example", 18082, 19444)
    command = stack.command("down")
    assert command[-3:] == ["--profile", "*", "down"]
    assert "--volumes" not in command
    assert command[command.index("--project-name") + 1] == "flowbridge-dev"
    assert "--profile" not in stack.command("up", "-d", "api")


def test_container_pins_match_native_toolchain_and_browser_lock() -> None:
    images = dotenv_values(ROOT / "infra/dev/images.env")
    native = json.loads((ROOT / "infra/identity/dependencies.json").read_text("utf-8"))
    package = json.loads((ROOT / "clients/sso/package.json").read_text("utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    expected = {
        "FLOWBRIDGE_PYTHON_IMAGE": "python:" + (ROOT / ".python-version").read_text().strip(),
        "FLOWBRIDGE_UV_IMAGE": "uv:" + project["tool"]["uv"]["required-version"].removeprefix("=="),
        "FLOWBRIDGE_KEYCLOAK_IMAGE": "keycloak:" + native["keycloak_version"],
        "FLOWBRIDGE_PLAYWRIGHT_IMAGE": "playwright:v"
        + package["devDependencies"]["@playwright/test"],
    }
    for key, version in expected.items():
        reference, digest = images[key].split("@sha256:")
        assert version in reference
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_generated_config_accepts_only_database_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "database.json"
    path.write_text(json.dumps({"FLOWBRIDGE_OIDC_ISSUER": "https://attacker.invalid"}), "utf-8")
    with pytest.raises(container.ContainerError, match="Invalid"):
        container.load_database(tmp_path)
    password = "test-only-literal-${NOT_INTERPOLATED}"
    monkeypatch.setenv("FLOWBRIDGE_DATABASE_PASSWORD", "old")
    path.write_text(json.dumps({"FLOWBRIDGE_DATABASE_PASSWORD": password}), "utf-8")
    container.load_database(tmp_path)
    assert container.os.environ["FLOWBRIDGE_DATABASE_PASSWORD"] == password


def test_preparation_reuses_profile_and_separates_db_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    monkeypatch.setenv("FLOWBRIDGE_DEV_TEST_MODE", "true")
    state, config, source = tmp_path / "state", tmp_path / "config", tmp_path / "private.env"
    password = "test-only-DB-password-with-$-and-#"
    source.write_text(
        f"FLOWBRIDGE_DATABASE_PASSWORD='{password}'\nFLOWBRIDGE_OIDC_CLIENT_SECRET=not-for-IAM-volume\n",
        "utf-8",
    )
    container.prepare_state(state, config, source)
    files = {
        p.relative_to(state).as_posix(): p.read_bytes() for p in state.rglob("*") if p.is_file()
    }
    assert all(password.encode() not in content for content in files.values())
    database = json.loads((config / "database.json").read_text("utf-8"))
    assert database == {"FLOWBRIDGE_DATABASE_PASSWORD": password}
    assert files["realm.json"] == files["import/flowbridge-realm.json"]
    assert json.loads(files["realm.json"])["accessTokenLifespan"] == 20
    container.prepare_state(state, config, source)
    assert {
        p.relative_to(state).as_posix(): p.read_bytes() for p in state.rglob("*") if p.is_file()
    } == files
    monkeypatch.setenv("FLOWBRIDGE_DEV_TEST_MODE", "false")
    with pytest.raises(container.ContainerError, match="another profile"):
        container.prepare_state(state, config, source)
    assert (state / "certs/server.key").read_bytes() == files["certs/server.key"]
    monkeypatch.setenv("FLOWBRIDGE_DEV_TEST_MODE", "true")
    (state / "fixture.json").write_text("{}", "utf-8")
    original_db = (config / "database.json").read_bytes()
    source.write_text("FLOWBRIDGE_DATABASE_PASSWORD=another-test-DB\n", "utf-8")
    with pytest.raises(container.ContainerError, match="Pending fixture"):
        container.prepare_state(state, config, source)
    assert (config / "database.json").read_bytes() == original_db


def test_partial_profile_is_never_overwritten(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "operator.json").write_text("{}", "utf-8")
    with pytest.raises(container.ContainerError, match="nonempty"):
        container.prepare_state(state, tmp_path / "config", tmp_path / "absent")
    assert (state / "operator.json").read_text("utf-8") == "{}"


def simulated_stack(monkeypatch: pytest.MonkeyPatch, failure: str = "", cleanup: int = 0) -> tuple:
    stack = dev.Stack(
        "flowbridge-test-abcdef012345", ROOT / ".env.example", 18082, 19444, test_mode=True
    )
    commands, phases = [], []
    monkeypatch.setattr(dev.socket, "socket", MagicMock())
    monkeypatch.setattr(stack, "save_run", phases.append)
    monkeypatch.setattr(stack, "has_volumes", lambda: False)

    def run(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
        commands.append(args)
        if failure == "revision" and "--require-head" in args:
            raise dev.StackError("DB revision mismatch")
        if failure == "browser" and args[-1] == "browser" and args[0] == "run":
            raise dev.StackError("Browser assertions failed")
        if failure == "interrupt" and args[-1] == "browser" and args[0] == "run":
            raise KeyboardInterrupt()
        return SimpleNamespace(returncode=cleanup if args[-1] == "cleanup" else 0, stdout="")

    monkeypatch.setattr(stack, "run", run)
    return stack, commands, phases


@pytest.mark.parametrize("failure", ["browser", "interrupt"])
def test_failed_or_interrupted_acceptance_always_cleans_fixture_and_preserves_failure(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    stack, commands, phases = simulated_stack(monkeypatch, failure)
    with pytest.raises(KeyboardInterrupt if failure == "interrupt" else dev.StackError):
        stack.test_sso()
    assert commands[-2][-1] == "cleanup"
    assert commands[-1] == ("down", "--volumes")
    assert phases[-1] == "failed-cleaned"


def test_unknown_cleanup_outcome_preserves_volumes_and_reports_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack, commands, phases = simulated_stack(monkeypatch, "browser", cleanup=1)
    with pytest.raises(dev.StackError, match="preserve volumes"):
        stack.test_sso()
    assert commands[-1] == ("down",)
    assert not any("--volumes" in command for command in commands)
    assert phases[-1] == "recovery-required"


def test_revision_mismatch_stops_before_iam_or_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    stack, commands, phases = simulated_stack(monkeypatch, "revision")
    with pytest.raises(dev.StackError, match="revision"):
        stack.test_sso()
    assert not any(command[0] == "up" or command[-1] == "fixture" for command in commands)
    assert commands[-1] == ("down", "--volumes")
    assert phases[-1] == "failed-cleaned"


def test_existing_project_volumes_are_never_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    stack, commands, _ = simulated_stack(monkeypatch)
    monkeypatch.setattr(stack, "has_volumes", lambda: True)
    with pytest.raises(dev.StackError, match="already has volumes"):
        stack.test_sso()
    assert commands == [("ps", "-a", "-q")]


def test_unavailable_engine_does_not_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = dev.Stack("flowbridge-dev", ROOT / ".env.example", 18082, 19444)
    monkeypatch.setattr(dev.shutil, "which", lambda _: None)
    with pytest.raises(dev.StackError, match="not installed"):
        stack.check_engine()
