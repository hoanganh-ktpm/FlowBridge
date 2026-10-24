"""Checks for the standalone Phase A deployment configuration."""

import os
import shutil
import stat
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from infra.deployment.readiness import check
from scripts.deployment_config import main, validate
from scripts.deployment_render import provision_sql, render

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = yaml.safe_load((ROOT / "deployment.example.yaml").read_text(encoding="utf-8"))
ENV = {
    "POSTGRES_PASSWORD": "test-only",
    "KEYCLOAK_ADMIN_PASSWORD": "test-only",
    "HASURA_ADMIN_SECRET": "test-only",
    "FLOWABLE_ADMIN_PASSWORD": "test-only",
    "OIDC_CLIENT_SECRET": "test-only",
    "CORE_DB_PASSWORD": "test-only",
    "SALES_DB_PASSWORD": "test-only",
    "WAREHOUSE_DB_PASSWORD": "test-only",
    "LOGISTICS_DB_PASSWORD": "test-only",
    "HASURA_DB_PASSWORD": "test-only",
    "FLOWABLE_DB_PASSWORD": "test-only",
    "KEYCLOAK_DB_PASSWORD": "test-only",
    "SALES_READER_PASSWORD": "test-only",
    "WAREHOUSE_READER_PASSWORD": "test-only",
    "LOGISTICS_READER_PASSWORD": "test-only",
}


def test_example_is_valid_with_secrets():
    assert validate(SAMPLE, ENV) == []


def test_multi_database_topology():
    assert SAMPLE["database"]["names"] == {
        "core": "dxos_core_db",
        "sales": "sales_db",
        "warehouse": "warehouse_db",
        "logistics": "logistics_db",
        "hasura_metadata": "hasura_metadata_db",
        "flowable": "flowable_db",
        "keycloak": "keycloak_db",
    }
    assert validate(SAMPLE, ENV) == []


@pytest.mark.parametrize("bad_name", ["sales db", "sales;DROP", "\tcore", "UPPER", "a" * 64])
def test_invalid_database_identifiers(bad_name):
    config = deepcopy(SAMPLE)
    config["database"]["names"]["sales"] = bad_name
    assert any("database.names.sales" in error for error in validate(config, ENV))


def test_invalid_database_user_and_duplicate_names():
    config = deepcopy(SAMPLE)
    config["database"]["user"] = "bad user"
    config["database"]["names"]["warehouse"] = "sales_db"
    errors = validate(config, ENV)
    assert any("database.user" in error for error in errors)
    assert any("database.names.warehouse must be unique" in error for error in errors)


def test_postgres_admin_must_not_be_runtime_role():
    config = deepcopy(SAMPLE)
    config["database"]["user"] = "core_owner"
    assert any(
        "database.user must not be a runtime role" in error for error in validate(config, ENV)
    )


def test_runtime_role_cannot_use_superuser_secret_reference():
    config = deepcopy(SAMPLE)
    config["database"]["role_password_env"]["core"] = "POSTGRES_PASSWORD"
    assert any(
        "database.role_password_env.core must use a separate variable" in error
        for error in validate(config, ENV)
    )


def test_phase_a_single_database_name_is_accepted():
    config = deepcopy(SAMPLE)
    config["database"].pop("names")
    config["database"]["name"] = "flowbridge"
    assert validate(config, ENV) == []


@pytest.mark.parametrize(
    "url",
    [
        "ftp://hasura:8080",
        "http://other:8080",
        "http://hasura:bad",
        "http://hasura:0",
        "http://hasura:8080/a b",
    ],
)
def test_invalid_internal_urls(url):
    config = deepcopy(SAMPLE)
    config["services"]["hasura"]["internal_url"] = url
    assert any("services.hasura.internal_url" in error for error in validate(config, ENV))


def test_external_internal_url_is_explicit():
    config = deepcopy(SAMPLE)
    config["services"]["hasura"]["internal_url"] = "https://data.partner.invalid"
    assert any("Docker service DNS" in error for error in validate(config, ENV))
    config["services"]["hasura"]["external"] = True
    assert validate(config, ENV) == []


def test_invalid_public_hostname():
    config = deepcopy(SAMPLE)
    config["services"]["hasura"]["public_url"] = "https://data..example.invalid"
    assert any("services.hasura.public_url" in error for error in validate(config, ENV))


def test_flowable_endpoint_uses_container_port_and_rest_base():
    flowable = SAMPLE["services"]["flowable"]
    assert flowable["port"] == 18083
    assert flowable["container_port"] == 18081
    assert flowable["internal_url"] == "http://flowable:18081/flowable-rest/service"
    config = deepcopy(SAMPLE)
    config["services"]["flowable"]["internal_url"] = "http://flowable:18083/flowable-rest"
    errors = validate(config, ENV)
    assert any("container_port" in error for error in errors)
    assert any("/flowable-rest/service" in error for error in errors)


def test_missing_secret_does_not_expose_value():
    errors = validate(SAMPLE, {**ENV, "POSTGRES_PASSWORD": ""})
    assert any("database.password_env" in error for error in errors)
    assert "test-only" not in str(errors)


def test_port_conflict_and_service_dependency():
    config = deepcopy(SAMPLE)
    config["services"]["hasura"]["port"] = config["services"]["keycloak"]["port"]
    config["services"]["postgres"]["enabled"] = False
    errors = validate(config, ENV)
    assert any("conflicts" in error for error in errors)
    assert any("requires services.postgres" in error for error in errors)


def test_production_issuer_tls_and_secret_refs():
    config = deepcopy(SAMPLE)
    config["environment"] = "production"
    config["identity"]["issuer"] = "https://wrong.example.invalid/realms/flowbridge"
    config["identity"]["client_secret_env"] = "inline-secret!"
    errors = validate(config, ENV)
    assert any("identity.issuer" in error for error in errors)
    assert any("tls.enabled" in error for error in errors)
    assert any("identity.client_secret_env" in error for error in errors)
    assert "inline-secret!" not in str(errors)


def test_cli_reads_env_without_docker(tmp_path, monkeypatch, capsys):
    config_file = tmp_path / "deployment.yaml"
    config_file.write_text(yaml.safe_dump(SAMPLE), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in ENV.items()), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv", ["deployment_config", "--config", str(config_file), "--env", str(env_file)]
    )
    assert main() == 0
    assert "PASS" in capsys.readouterr().out


def test_phase_b_requires_keycloak_database_and_role_secret():
    config = deepcopy(SAMPLE)
    config["database"]["names"].pop("keycloak")
    config["database"]["role_password_env"].pop("keycloak")
    errors = validate(config, ENV)
    assert any("database.names.keycloak" in error for error in errors)
    assert any("database.role_password_env.keycloak" in error for error in errors)


def test_provisioning_is_additive_and_has_separate_roles():
    sql = provision_sql(SAMPLE)
    for role in (
        "core_owner",
        "sales_owner",
        "warehouse_owner",
        "logistics_owner",
        "hasura_owner",
        "wf_engine",
        "keycloak_owner",
        "sales_reader",
    ):
        assert role in sql
    for name in SAMPLE["database"]["names"].values():
        assert name in sql
    assert "WHERE NOT EXISTS" in sql
    assert r"\if :missing_db" in sql
    assert "ALTER ROLE" not in sql
    assert "DROP " not in sql
    assert "NOT rolsuper" in sql
    assert "NOT rolreplication" in sql
    assert "NOT rolbypassrls" in sql
    assert "aclexplode" in sql
    assert "has_database_privilege" in sql
    assert 'GRANT CONNECT ON DATABASE "sales_db" TO "sales_reader"' in sql
    assert "GRANT SELECT" not in sql
    assert "test-only" not in sql


def test_rendered_stack_has_private_ports_volumes_and_no_inline_secrets(tmp_path):
    path = render(SAMPLE, ENV, tmp_path)
    compose_text = path.read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)
    assert set(compose["services"]) == {"postgres", "provision", "keycloak", "hasura", "flowable"}
    assert compose["services"]["postgres"]["volumes"][0].endswith(":/var/lib/postgresql/data")
    assert compose["services"]["postgres"]["restart"] == "unless-stopped"
    assert all(
        port.startswith("127.0.0.1:")
        for service in compose["services"].values()
        for port in service.get("ports", [])
    )
    assert (
        compose["services"]["keycloak"]["depends_on"]["provision"]["condition"]
        == "service_completed_successfully"
    )
    assert compose["services"]["flowable"]["ports"] == ["127.0.0.1:18083:18081"]
    assert compose["services"]["hasura"]["depends_on"]["keycloak"]["condition"] == "service_healthy"
    assert compose["services"]["keycloak"]["healthcheck"]["test"][1] == "bash"
    assert "test-only" not in compose_text
    assert "test-only" not in (tmp_path / "provision.sql").read_text(encoding="utf-8")
    assert "KC_DB_PASSWORD=test-only" in (tmp_path / "keycloak.env").read_text(encoding="utf-8")
    assert render(SAMPLE, ENV, tmp_path) == path


def test_render_does_not_silently_rotate_existing_database_passwords(tmp_path):
    render(SAMPLE, ENV, tmp_path)
    changed = {**ENV, "CORE_DB_PASSWORD": "new-test-only"}
    with pytest.raises(ValueError, match="rotate them explicitly"):
        render(SAMPLE, changed, tmp_path)


def test_disabled_flowable_is_not_deployed(tmp_path):
    config = deepcopy(SAMPLE)
    config["services"]["flowable"]["enabled"] = False
    compose = yaml.safe_load(render(config, ENV, tmp_path).read_text(encoding="utf-8"))
    assert "flowable" not in compose["services"]
    assert "flowable_db" not in (tmp_path / "provision.sql").read_text(encoding="utf-8")


def test_render_rejects_unwired_image_container_port(tmp_path):
    config = deepcopy(SAMPLE)
    config["services"]["hasura"]["internal_url"] = "http://hasura:9999"
    with pytest.raises(ValueError, match="container port"):
        render(config, ENV, tmp_path)


def test_production_render_never_uses_start_dev(tmp_path):
    config = deepcopy(SAMPLE)
    config["environment"] = "production"
    config["tls"]["enabled"] = True
    config["services"]["flowable"]["schema_policy"] = "manual"
    config["services"]["flowable"]["schema_prepared"] = True
    compose = yaml.safe_load(render(config, ENV, tmp_path).read_text(encoding="utf-8"))
    assert compose["services"]["keycloak"]["command"] == ["start", "--import-realm"]
    assert "FLOWABLE_DATABASE_SCHEMA_UPDATE=false" in (tmp_path / "flowable.env").read_text()


def test_flowable_schema_policy_blocks_unsafe_environments():
    config = deepcopy(SAMPLE)
    config["environment"] = "production"
    assert any("development-only" in error for error in validate(config, ENV))
    config["services"]["flowable"]["schema_policy"] = "manual"
    assert any("schema_prepared" in error for error in validate(config, ENV))
    config["services"]["flowable"]["schema_prepared"] = True
    assert not any("schema_" in error for error in validate(config, ENV))


def test_keycloak_claim_mappers_and_jwt_match(tmp_path):
    render(SAMPLE, ENV, tmp_path)
    realm = yaml.safe_load((tmp_path / "realm.json").read_text())
    mappers = realm["clients"][0]["protocolMappers"]
    claims = {mapper["config"].get("claim.name"): mapper for mapper in mappers}
    prefix = "https://hasura\\.io/jwt/claims."
    assert (
        claims[prefix + "x-hasura-allowed-roles"]["protocolMapper"]
        == "oidc-usermodel-client-role-mapper"
    )
    assert (
        claims[prefix + "x-hasura-default-role"]["config"]["user.attribute"]
        == "hasura_default_role"
    )
    assert claims[prefix + "x-hasura-user-id"]["protocolMapper"] == "oidc-usermodel-property-mapper"
    assert not any("admin" in str(mapper).lower() for mapper in mappers)
    hasura = dict(
        line.split("=", 1)
        for line in (tmp_path / "hasura.env").read_text().splitlines()
        if "=" in line
    )
    jwt = yaml.safe_load(hasura["HASURA_GRAPHQL_JWT_SECRET"])
    assert jwt["issuer"] == SAMPLE["identity"]["issuer"]
    assert jwt["audience"] == SAMPLE["identity"]["client_id"]
    assert jwt["jwk_url"].startswith(SAMPLE["services"]["keycloak"]["internal_url"])
    with pytest.raises(ValueError, match="existing realm import differs"):
        changed = deepcopy(SAMPLE)
        changed["identity"]["hasura_default_role_attribute"] = "other_role"
        render(changed, ENV, tmp_path)


def test_secret_permissions_and_no_silent_rotation(tmp_path):
    render(SAMPLE, ENV, tmp_path)
    secret = tmp_path / "provision.env"
    if os.name != "nt":
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    previous = secret.stat().st_mtime_ns
    render(SAMPLE, ENV, tmp_path)
    assert secret.stat().st_mtime_ns == previous
    with pytest.raises(ValueError) as error:
        render(SAMPLE, {**ENV, "HASURA_ADMIN_SECRET": "another-secret"}, tmp_path)
    assert "another-secret" not in str(error.value)


def test_application_readiness_probes(tmp_path, monkeypatch):
    render(SAMPLE, ENV, tmp_path)
    seen = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"status":"UP"}'

    def fake_open(request, timeout):
        seen.append((request.full_url, request.headers))
        return Response()

    monkeypatch.setattr("infra.deployment.readiness.urlopen", fake_open)
    assert check(tmp_path) == []
    assert seen[0][0].endswith("/healthz")
    assert seen[1][0].endswith("/flowable-rest/actuator/health")
    assert "Authorization" in seen[1][1]

    def unavailable(request, timeout):
        raise OSError("unavailable")

    monkeypatch.setattr("infra.deployment.readiness.urlopen", unavailable)
    assert check(tmp_path) == ["hasura", "flowable"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI unavailable")
def test_rendered_docker_compose_config(tmp_path):
    path = render(SAMPLE, ENV, tmp_path)
    result = subprocess.run(
        ["docker", "compose", "-f", str(path), "config", "--quiet"],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
