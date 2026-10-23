import json
from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values
from scripts import docker_env

ROOT = Path(__file__).resolve().parents[2]


def test_docker_engines_are_pinned_and_only_publish_loopback_ports():
    compose = yaml.safe_load((ROOT / "infra/docker-compose.yml").read_text())
    assert compose["name"] == "flowbridge-integration"
    assert set(compose["services"]) == {"keycloak", "hasura"}
    for service in compose["services"].values():
        assert len(service["image"].split("@sha256:")[1]) == 64
        assert all(port.startswith("127.0.0.1:") for port in service.get("ports", []))
        assert "privileged" not in service and "container_name" not in service
    assert set(compose["volumes"]) == {"keycloak_data"}
    hasura = compose["services"]["hasura"]
    assert hasura["env_file"][0].endswith("/hasura.env")
    assert "HASURA_GRAPHQL_DATABASE_URL" not in hasura["environment"]


def test_preparation_preserves_env_and_reuses_existing_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(docker_env, "__file__", str(tmp_path / "scripts/docker_env.py"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "programs"))
    monkeypatch.setenv("DXOS_CORE_URL", "postgresql://synthetic@localhost/dxos_core_db")
    template = tmp_path / "infra/keycloak/realm.template.json"
    template.parent.mkdir(parents=True)
    template.write_text((ROOT / "infra/keycloak/realm.template.json").read_text())
    openssl = tmp_path / "programs/Git/usr/bin/openssl.exe"
    openssl.parent.mkdir(parents=True)
    openssl.touch()
    original = "PRESERVE_EXISTING_CONFIG=yes\n"
    (tmp_path / ".env").write_text(original)
    calls = []
    monkeypatch.setattr(docker_env.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    docker_env.prepare()
    state = (tmp_path / ".tmp/docker-integration/secrets.json").read_bytes()
    env = dotenv_values(tmp_path / ".env.docker")
    assert json.loads(env["FLOWBRIDGE_HASURA_JWT_CONFIG"])["type"] == "HS256"
    realm = json.loads(
        (template.parents[2] / ".tmp/docker-integration/realm/flowbridge-it-realm.json").read_text()
    )
    assert realm["realm"] == "flowbridge-it"
    assert next(client for client in realm["clients"] if client["clientId"] == "flowbridge-e2e")[
        "directAccessGrantsEnabled"
    ]
    docker_env.prepare()
    assert len(calls) == 1
    assert (tmp_path / ".env").read_text() == original
    assert (tmp_path / ".tmp/docker-integration/secrets.json").read_bytes() == state


def test_preparation_does_not_overwrite_unknown_env_file(tmp_path, monkeypatch):
    monkeypatch.setattr(docker_env, "__file__", str(tmp_path / "scripts/docker_env.py"))
    (tmp_path / ".env.docker").write_text("DO_NOT_OVERWRITE=yes\n")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        docker_env.prepare()
