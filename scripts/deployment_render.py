"""Render the Phase B Compose stack; this does not start Docker or rotate secrets."""

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse

import yaml

from scripts.deployment_config import DB_IDENTIFIER, _env_file, validate

IMAGES = {
    "postgres": "postgres:16",
    "keycloak": (
        "quay.io/keycloak/keycloak:26.8.1@sha256:"
        "a78d6b79572168cb25886b80a8b5b8d4ed4390743e3eecb810538683e209dd6b"
    ),
    "hasura": (
        "hasura/graphql-engine:v2.48.16@sha256:"
        "6ff7d8ba122c69f7be377092b0c1f5314d3e4e28274dc089cd774ce6a8ba6479"
    ),
    "flowable": (
        "flowable/flowable-rest:7.2.0@sha256:"
        "bcb895b12a95c7a11c07daef9a66c7c7e021038963b0877ee1f01d43059694ee"
    ),
}
OWNERS = {
    "core": "core_owner",
    "sales": "sales_owner",
    "warehouse": "warehouse_owner",
    "logistics": "logistics_owner",
    "hasura_metadata": "hasura_owner",
    "flowable": "wf_engine",
    "keycloak": "keycloak_owner",
}
READERS = ("sales_reader", "warehouse_reader", "logistics_reader")


def _role_keys(config):
    enabled = config["services"]
    return [
        key for key in OWNERS if key not in ("flowable", "keycloak") or enabled[key]["enabled"]
    ] + list(READERS)


def provision_sql(config):
    """Generate only creation commands for missing objects; never ALTER existing roles."""
    names = config["database"]["names"]
    if any(
        not isinstance(name, str) or not DB_IDENTIFIER.fullmatch(name) for name in names.values()
    ):
        raise ValueError("invalid database identifier")
    lines = [r"\set ON_ERROR_STOP on", "-- Run on every startup, including existing volumes."]
    for key in _role_keys(config):
        role = OWNERS.get(key, key)
        variable = f"pw_{key}"
        lines.extend(
            [
                rf"\getenv {variable} ROLE_{key.upper()}_PASSWORD",
                (
                    "SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB "
                    f"NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L', '{role}', :'{variable}')"
                ),
                f"WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') \\gexec",
                "DO $$BEGIN",
                (
                    "  IF NOT EXISTS (SELECT FROM pg_roles "
                    f"WHERE rolname = '{role}' AND rolcanlogin AND NOT rolsuper "
                    "AND NOT rolcreatedb AND NOT rolcreaterole "
                    "AND NOT rolreplication AND NOT rolbypassrls) THEN"
                ),
                f"    RAISE EXCEPTION 'unsafe role attributes: {role}';",
                "  END IF;",
                "END$$;",
            ]
        )
    for key, role in OWNERS.items():
        if key in ("flowable", "keycloak") and not config["services"][key]["enabled"]:
            continue
        name = names[key]
        lines.extend(
            [
                (
                    "SELECT NOT EXISTS (SELECT FROM pg_database "
                    f"WHERE datname = '{name}') AS missing_db \\gset"
                ),
                r"\if :missing_db",
                f'CREATE DATABASE "{name}" OWNER "{role}";',
                f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC;',
                r"\endif",
                "DO $$BEGIN",
                (
                    "  IF (SELECT pg_get_userbyid(datdba) FROM pg_database "
                    f"WHERE datname = '{name}') <> '{role}' THEN"
                ),
                f"    RAISE EXCEPTION 'database owner mismatch: {key}';",
                "  END IF;",
                "  IF EXISTS (",
                "    SELECT 1 FROM pg_database d",
                "    CROSS JOIN LATERAL aclexplode(",
                "      COALESCE(d.datacl, acldefault('d', d.datdba))",
                "    ) a",
                f"    WHERE d.datname = '{name}' AND a.grantee = 0",
                "      AND a.privilege_type IN ('CONNECT', 'CREATE', 'TEMPORARY')",
                "  ) THEN",
                f"    RAISE EXCEPTION 'unsafe PUBLIC database privileges: {key}';",
                "  END IF;",
                "END$$;",
            ]
        )
        if key in ("sales", "warehouse", "logistics"):
            lines.append(f'GRANT CONNECT ON DATABASE "{name}" TO "{key}_reader";')
            lines.extend(
                [
                    "DO $$BEGIN",
                    f"  IF has_database_privilege('{key}_reader', '{name}', 'CREATE')",
                    f"     OR has_database_privilege('{key}_reader', '{name}', 'TEMPORARY') THEN",
                    f"    RAISE EXCEPTION 'unsafe reader database privileges: {key}';",
                    "  END IF;",
                    "END$$;",
                ]
            )
    return "\n".join(lines) + "\n"


def _env_text(values):
    if any("\n" in str(value) or "\r" in str(value) for value in values.values()):
        raise ValueError("deployment environment values must be single-line")
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _db_url(user, password, name):
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@postgres:5432/{name}"


def render(config, env, output):
    errors = validate(config, env, output)
    if errors:
        raise ValueError("; ".join(errors))
    database = config["database"]
    if not isinstance(database.get("names"), dict):
        raise ValueError(
            "Phase B requires database.names; legacy database.name remains validation-only"
        )
    if config["tls"]["enabled"] and config["tls"].get("termination") != "external":
        raise ValueError("Phase B supports TLS termination by an external reverse proxy only")
    if not config["services"]["postgres"]["enabled"]:
        raise ValueError("Phase B requires managed PostgreSQL")
    if any(service.get("external") for service in config["services"].values()):
        raise ValueError("Phase B does not deploy external service endpoints")
    for name in ("keycloak", "hasura"):
        if config["services"][name]["enabled"]:
            internal = urlparse(config["services"][name]["internal_url"])
            if internal.port != 8080:
                raise ValueError(f"{name} internal_url must use the image's container port")
    if config["services"]["keycloak"]["enabled"]:
        keycloak = config["services"]["keycloak"]
        if urlparse(keycloak["internal_url"]).path not in ("", "/") or urlparse(
            keycloak["public_url"]
        ).path not in ("", "/"):
            raise ValueError("Keycloak path prefixes are not supported by Phase B")

    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)

    def write(name, content, protected=(), allow_new=False, sensitive=False):
        path = output / name
        if path.exists():
            if (
                (protected or sensitive)
                and os.name != "nt"
                and stat.S_IMODE(path.stat().st_mode) & 0o077
            ):
                raise ValueError("existing secret file permissions are too broad")
            previous = path.read_text(encoding="utf-8")
            if previous == content:
                return
            if protected:
                old_values = dict(
                    line.split("=", 1) for line in previous.splitlines() if "=" in line
                )
                new_values = dict(
                    line.split("=", 1) for line in content.splitlines() if "=" in line
                )
                if any(
                    key in old_values
                    and old_values[key] != new_values.get(key)
                    or key not in old_values
                    and not allow_new
                    for key in protected
                ):
                    raise ValueError("existing credentials differ; rotate them explicitly")
        descriptor, temporary = tempfile.mkstemp(prefix=".render-", dir=output)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    names = database["names"]

    def password(key):
        return env[database["role_password_env"][key]]

    provision_env = {
        "PGUSER": database["user"],
        "PGPASSWORD": env[database["password_env"]],
    }
    for key in _role_keys(config):
        provision_env[f"ROLE_{key.upper()}_PASSWORD"] = password(key)
    write(
        "provision.env",
        _env_text(provision_env),
        protected=tuple(provision_env),
        allow_new=True,
    )
    write(
        "postgres.env",
        _env_text(
            {
                "POSTGRES_USER": database["user"],
                "POSTGRES_PASSWORD": env[database["password_env"]],
                "POSTGRES_DB": "postgres",
            }
        ),
        protected=("POSTGRES_PASSWORD",),
    )
    write("provision.sql", provision_sql(config))

    services = config["services"]
    compose = {
        "name": config["name"],
        "services": {
            "postgres": {
                "image": IMAGES["postgres"],
                "env_file": [{"path": "./postgres.env", "format": "raw"}],
                "ports": [f"127.0.0.1:{services['postgres']['port']}:5432"],
                "volumes": [f"{config['volumes']['postgres']}:/var/lib/postgresql/data"],
                "networks": ["internal"],
                "healthcheck": {
                    "test": ["CMD-SHELL", f"pg_isready -U {database['user']} -d postgres"],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 20,
                },
                "restart": "unless-stopped",
            },
            "provision": {
                "image": IMAGES["postgres"],
                "env_file": [{"path": "./provision.env", "format": "raw"}],
                "command": [
                    "sh",
                    "-ec",
                    'exec psql -h postgres -U "$$PGUSER" -d postgres -f /run/provision.sql',
                ],
                "volumes": ["./provision.sql:/run/provision.sql:ro"],
                "depends_on": {"postgres": {"condition": "service_healthy"}},
                "networks": ["internal"],
                "restart": "no",
            },
        },
        "networks": {"internal": {}},
        "volumes": {config["volumes"]["postgres"]: {}},
    }
    provisioned = {"provision": {"condition": "service_completed_successfully"}}
    if services["keycloak"]["enabled"]:
        identity = config["identity"]
        claims = "https://hasura\\.io/jwt/claims."
        realm = {
            "realm": identity["realm"],
            "enabled": True,
            "sslRequired": "all" if config["environment"] == "production" else "external",
            "registrationAllowed": False,
            "clients": [
                {
                    "clientId": identity["client_id"],
                    "enabled": True,
                    "protocol": "openid-connect",
                    "publicClient": False,
                    "standardFlowEnabled": False,
                    "secret": env[identity["client_secret_env"]],
                    "protocolMappers": [
                        {
                            "name": "hasura-allowed-roles",
                            "protocol": "openid-connect",
                            "protocolMapper": "oidc-usermodel-client-role-mapper",
                            "config": {
                                "usermodel.clientRoleMapping.clientId": identity["client_id"],
                                "multivalued": "true",
                                "claim.name": claims + "x-hasura-allowed-roles",
                                "jsonType.label": "String",
                                "access.token.claim": "true",
                                "id.token.claim": "false",
                            },
                        },
                        {
                            "name": "hasura-default-role",
                            "protocol": "openid-connect",
                            "protocolMapper": "oidc-usermodel-attribute-mapper",
                            "config": {
                                "user.attribute": identity.get(
                                    "hasura_default_role_attribute", "hasura_default_role"
                                ),
                                "claim.name": claims + "x-hasura-default-role",
                                "jsonType.label": "String",
                                "access.token.claim": "true",
                                "id.token.claim": "false",
                            },
                        },
                        {
                            "name": "hasura-user-id",
                            "protocol": "openid-connect",
                            "protocolMapper": "oidc-usermodel-property-mapper",
                            "config": {
                                "user.attribute": "id",
                                "claim.name": claims + "x-hasura-user-id",
                                "jsonType.label": "String",
                                "access.token.claim": "true",
                                "id.token.claim": "false",
                            },
                        },
                        {
                            "name": "flowbridge-api-audience",
                            "protocol": "openid-connect",
                            "protocolMapper": "oidc-audience-mapper",
                            "config": {
                                "included.client.audience": identity["client_id"],
                                "access.token.claim": "true",
                                "id.token.claim": "false",
                            },
                        },
                    ],
                }
            ],
        }
        realm_text = json.dumps(realm, indent=2) + "\n"
        existing_realm = output / "realm.json"
        if existing_realm.exists() and existing_realm.read_text(encoding="utf-8") != realm_text:
            raise ValueError("existing realm import differs; update Keycloak realm manually")
        write("realm.json", realm_text, sensitive=True)
        keycloak_env = {
            "KC_DB": "postgres",
            "KC_DB_URL": f"jdbc:postgresql://postgres:5432/{names['keycloak']}",
            "KC_DB_USERNAME": OWNERS["keycloak"],
            "KC_DB_PASSWORD": password("keycloak"),
            "KC_BOOTSTRAP_ADMIN_USERNAME": config["credentials"]["keycloak_admin_user"],
            "KC_BOOTSTRAP_ADMIN_PASSWORD": env[config["secrets"]["keycloak_admin_password_env"]],
            "KC_HOSTNAME": services["keycloak"]["public_url"],
            "KC_HTTP_ENABLED": "true",
            "KC_HEALTH_ENABLED": "true",
        }
        if config["tls"]["enabled"]:
            keycloak_env["KC_PROXY_HEADERS"] = "xforwarded"
        write(
            "keycloak.env",
            _env_text(keycloak_env),
            protected=("KC_DB_PASSWORD", "KC_BOOTSTRAP_ADMIN_PASSWORD"),
        )
        compose["services"]["keycloak"] = {
            "image": IMAGES["keycloak"],
            "command": [
                "start-dev" if config["environment"] == "development" else "start",
                "--import-realm",
            ],
            "env_file": [{"path": "./keycloak.env", "format": "raw"}],
            "ports": [f"127.0.0.1:{services['keycloak']['port']}:8080"],
            "volumes": ["./realm.json:/opt/keycloak/data/import/realm.json:ro"],
            "depends_on": provisioned,
            "networks": ["internal"],
            "healthcheck": {
                "test": [
                    "CMD",
                    "bash",
                    "-c",
                    (
                        "{ printf 'HEAD /health/ready HTTP/1.0\\r\\n\\r\\n' >&0; "
                        "grep 'HTTP/1.0 200'; } 0<>/dev/tcp/127.0.0.1/9000"
                    ),
                ],
                "interval": "10s",
                "timeout": "5s",
                "retries": 30,
            },
            "restart": "unless-stopped",
        }

    if services["hasura"]["enabled"]:
        identity = config["identity"]
        keycloak_internal = services["keycloak"]["internal_url"].rstrip("/")
        jwt = {
            "type": "RS256",
            "jwk_url": (
                f"{keycloak_internal}/realms/{identity['realm']}/protocol/openid-connect/certs"
            ),
            "issuer": identity["issuer"],
            "audience": identity["client_id"],
        }
        write(
            "hasura.env",
            _env_text(
                {
                    "HASURA_GRAPHQL_METADATA_DATABASE_URL": _db_url(
                        OWNERS["hasura_metadata"],
                        password("hasura_metadata"),
                        names["hasura_metadata"],
                    ),
                    "HASURA_GRAPHQL_ADMIN_SECRET": env[
                        config["secrets"]["hasura_admin_secret_env"]
                    ],
                    "HASURA_GRAPHQL_JWT_SECRET": json.dumps(jwt, separators=(",", ":")),
                    "HASURA_GRAPHQL_ENABLE_CONSOLE": "false",
                    "HASURA_GRAPHQL_ENABLE_TELEMETRY": "false",
                }
            ),
            protected=("HASURA_GRAPHQL_ADMIN_SECRET",),
        )
        compose["services"]["hasura"] = {
            "image": IMAGES["hasura"],
            "env_file": [{"path": "./hasura.env", "format": "raw"}],
            "ports": [f"127.0.0.1:{services['hasura']['port']}:8080"],
            "depends_on": {
                **provisioned,
                "keycloak": {"condition": "service_healthy"},
            },
            "networks": ["internal"],
            "restart": "unless-stopped",
        }

    if services["flowable"]["enabled"]:
        flowable = services["flowable"]
        container_port = flowable["container_port"]
        write(
            "flowable.env",
            _env_text(
                {
                    "SPRING_DATASOURCE_URL": f"jdbc:postgresql://postgres:5432/{names['flowable']}",
                    "SPRING_DATASOURCE_USERNAME": OWNERS["flowable"],
                    "SPRING_DATASOURCE_PASSWORD": password("flowable"),
                    "SERVER_PORT": str(container_port),
                    "FLOWABLE_REST_APP_ADMIN_USER_ID": config["credentials"]["flowable_admin_user"],
                    "FLOWABLE_REST_APP_ADMIN_PASSWORD": env[
                        config["secrets"]["flowable_admin_password_env"]
                    ],
                    "FLOWABLE_REST_APP_CREATE_DEMO_DEFINITIONS": "false",
                    "FLOWABLE_REST_APP_SWAGGER_DOCS_ENABLED": "false",
                    "FLOWABLE_DATABASE_SCHEMA_UPDATE": (
                        "true" if flowable["schema_policy"] == "update" else "false"
                    ),
                }
            ),
            protected=("SPRING_DATASOURCE_PASSWORD", "FLOWABLE_REST_APP_ADMIN_PASSWORD"),
        )
        compose["services"]["flowable"] = {
            "image": IMAGES["flowable"],
            "env_file": [{"path": "./flowable.env", "format": "raw"}],
            "ports": [f"127.0.0.1:{flowable['port']}:{container_port}"],
            "depends_on": provisioned,
            "networks": ["internal"],
            "restart": "unless-stopped",
        }

    write("compose.yaml", yaml.safe_dump(compose, sort_keys=False))
    return output / "compose.yaml"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("deployment.yaml"))
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=Path("infra/deployment/generated"))
    args = parser.parse_args()
    try:
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
        path = render(config, _env_file(args.env), args.output)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        # Diagnostics never include credential values or YAML parser excerpts.
        print(f"FAIL: deployment render ({type(exc).__name__})")
        return 1
    print(f"PASS: rendered {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
