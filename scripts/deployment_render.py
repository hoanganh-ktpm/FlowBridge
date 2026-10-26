"""Render the Phase B Compose stack; this does not start Docker or rotate secrets."""

import argparse
import hashlib
import hmac
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
PRODUCT_VIEWS = {
    "sales": ("v_confirmed_demand_daily", "v_open_orders", "v_planning_demand_daily"),
    "warehouse": (
        "v_stock_availability",
        "v_inbound_schedule",
        "v_order_commitments",
        "v_planning_catalog",
        "v_receipts_daily",
    ),
    "logistics": ("v_handoff_capacity", "v_accepted_shipments_daily", "v_handoff_schedule"),
}


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


def _driver_db_url(user, password, name, driver="psycopg"):
    return _db_url(user, password, name).replace("postgresql://", f"postgresql+{driver}://", 1)


def _source_name(application_key, source_key="pg"):
    encoded = json.dumps([application_key, source_key], separators=(",", ":"))
    suffix = hashlib.sha256(encoded.encode()).hexdigest()[:10]
    return f"{application_key}_{source_key}_{suffix}"


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
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
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
    write(
        "postgres-start.sh",
        """#!/usr/bin/env bash
set -Eeuo pipefail
# Keep the generated launcher LF-only so it is executable from Windows checkouts.
certdir=/run/postgres-certs
mkdir -p \"$certdir\"
if [ ! -s \"$certdir/server.key\" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj /CN=postgres \\
    -addext subjectAltName=DNS:postgres -keyout \"$certdir/server.key\" \\
    -out \"$certdir/server.crt\" >/dev/null 2>&1
fi
chown postgres:postgres \"$certdir/server.key\" \"$certdir/server.crt\"
chmod 600 \"$certdir/server.key\"
exec docker-entrypoint.sh postgres -c ssl=on \\
  -c ssl_cert_file=\"$certdir/server.crt\" -c ssl_key_file=\"$certdir/server.key\"
""",
    )

    services = config["services"]
    compose = {
        "name": config["name"],
        "services": {
            "postgres": {
                "image": IMAGES["postgres"],
                "entrypoint": ["bash", "/run/postgres-start.sh"],
                "env_file": [{"path": "./postgres.env", "format": "raw"}],
                "ports": [f"127.0.0.1:{services['postgres']['port']}:5432"],
                "volumes": [
                    f"{config['volumes']['postgres']}:/var/lib/postgresql/data",
                    "./postgres-start.sh:/run/postgres-start.sh:ro",
                ],
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
                # The upstream image declares /var/lib/postgresql/data as VOLUME.
                # Override it for this stateless client job so Docker does not
                # create an unlabeled anonymous volume that `compose down -v`
                # would later remove without appearing in the Compose model.
                "tmpfs": ["/var/lib/postgresql/data"],
                "depends_on": {"postgres": {"condition": "service_healthy"}},
                "networks": ["internal"],
                "restart": "no",
            },
            "migrate": {
                "build": {"context": "../../..", "dockerfile": "infra/deployment/Dockerfile.core"},
                "image": "flowbridge-core:local",
                "env_file": [{"path": "./migration.env", "format": "raw"}],
                "command": [
                    "sh",
                    "-ec",
                    "python -m scripts.database bootstrap && "
                    "python -m scripts.database grant-readers",
                ],
                "depends_on": {"provision": {"condition": "service_completed_successfully"}},
                "networks": ["internal"],
                "restart": "no",
            },
        },
        "networks": {"internal": {}},
        "volumes": {config["volumes"]["postgres"]: {}},
    }
    provisioned = {"provision": {"condition": "service_completed_successfully"}}
    migrated = {"migrate": {"condition": "service_completed_successfully"}}
    migration_env = {"DXOS_LAYOUT": "databases"}
    for key in ("core", "sales", "warehouse", "logistics"):
        migration_env[f"DXOS_{key.upper()}_URL"] = _driver_db_url(
            OWNERS[key], password(key), names[key]
        )
    migration_env["DXOS_ADMIN_URL"] = _driver_db_url(
        database["user"], env[database["password_env"]], "postgres"
    )
    write("migration.env", _env_text(migration_env), protected=tuple(migration_env), allow_new=True)
    if services["keycloak"]["enabled"]:
        identity = config["identity"]
        claims = "https://hasura\\.io/jwt/claims."
        acceptance_clients = (
            ("flowbridge-e2e-it", "flowbridge-e2e-consumer", "flowbridge-e2e-outsider")
            if config["environment"] == "development"
            else ()
        )
        acceptance_secrets = {
            client: hmac.new(
                env[identity["client_secret_env"]].encode(),
                ("flowbridge/acceptance-client/" + client).encode(),
                hashlib.sha256,
            ).hexdigest()
            for client in acceptance_clients
        }
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
                    "serviceAccountsEnabled": True,
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
                },
                {
                    "clientId": identity.get("web_client_id", "flowbridge-web"),
                    "enabled": True,
                    "protocol": "openid-connect",
                    "publicClient": True,
                    "standardFlowEnabled": True,
                    "directAccessGrantsEnabled": False,
                    "redirectUris": identity.get("web_redirect_uris", []),
                    "webOrigins": [
                        uri.removesuffix("/*") for uri in identity.get("web_redirect_uris", [])
                    ],
                    "attributes": {"pkce.code.challenge.method": "S256"},
                },
                *[
                    {
                        "clientId": client,
                        "enabled": True,
                        "protocol": "openid-connect",
                        "publicClient": False,
                        "standardFlowEnabled": False,
                        "directAccessGrantsEnabled": False,
                        "serviceAccountsEnabled": True,
                        "secret": acceptance_secrets[client],
                        "protocolMappers": [
                            {
                                "name": "flowbridge-api-audience",
                                "protocol": "openid-connect",
                                "protocolMapper": "oidc-audience-mapper",
                                "config": {
                                    "included.client.audience": identity["client_id"],
                                    "access.token.claim": "true",
                                    "id.token.claim": "false",
                                },
                            }
                        ],
                    }
                    for client in acceptance_clients
                ],
            ],
        }
        realm_text = json.dumps(realm, indent=2) + "\n"
        existing_realm = output / "realm.json"
        if existing_realm.exists() and existing_realm.read_text(encoding="utf-8") != realm_text:
            raise ValueError("existing realm import differs; update Keycloak realm manually")
        write("realm.json", realm_text, sensitive=True)
        if acceptance_secrets:
            write(
                "acceptance.env",
                _env_text(acceptance_secrets),
                protected=tuple(acceptance_secrets),
                allow_new=True,
            )
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
        service_clients = [identity["client_id"]]
        if config["environment"] == "development":
            service_clients.extend(
                ["flowbridge-e2e-it", "flowbridge-e2e-consumer", "flowbridge-e2e-outsider"]
            )
        allowed_clients = [*service_clients, identity.get("web_client_id", "flowbridge-web")]
        keycloak_internal = services["keycloak"]["internal_url"].rstrip("/")
        signing_key = hmac.new(
            env[identity["client_secret_env"]].encode(),
            b"flowbridge/hasura-governed-jwt/v1",
            hashlib.sha256,
        ).hexdigest()
        jwt = {
            "type": "HS256",
            "key": signing_key,
            "issuer": "flowbridge-core",
            "audience": "flowbridge-data",
        }
        hasura_env = {
            "HASURA_GRAPHQL_METADATA_DATABASE_URL": _db_url(
                OWNERS["hasura_metadata"],
                password("hasura_metadata"),
                names["hasura_metadata"],
            ),
            "HASURA_GRAPHQL_ADMIN_SECRET": env[config["secrets"]["hasura_admin_secret_env"]],
            "HASURA_GRAPHQL_JWT_SECRET": json.dumps(jwt, separators=(",", ":")),
            "HASURA_GRAPHQL_ENABLE_CONSOLE": "false",
            "HASURA_GRAPHQL_ENABLE_TELEMETRY": "false",
        }
        for domain in PRODUCT_VIEWS:
            hasura_env[f"{domain.upper()}_READ_DATABASE_URL"] = (
                _db_url(f"{domain}_reader", password(f"{domain}_reader"), names[domain])
                + "?sslmode=require"
            )
        write(
            "hasura.env",
            _env_text(hasura_env),
            protected=tuple(hasura_env),
            allow_new=True,
        )
        compose["services"]["hasura"] = {
            "image": IMAGES["hasura"],
            "env_file": [{"path": "./hasura.env", "format": "raw"}],
            "ports": [f"127.0.0.1:{services['hasura']['port']}:8080"],
            "depends_on": {
                **migrated,
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

    if services["core"]["enabled"]:
        identity = config["identity"]
        signing_key = hmac.new(
            env[identity["client_secret_env"]].encode(),
            b"flowbridge/hasura-governed-jwt/v1",
            hashlib.sha256,
        ).hexdigest()
        scopes = {
            f"{domain}-reader": [{"schema": "public", "name": view} for view in views]
            for domain, views in PRODUCT_VIEWS.items()
        }
        from scripts.database.products import view_keys

        secret_bindings = {
            f"{domain}-reader": f"{domain.upper()}_READ_DATABASE_URL" for domain in PRODUCT_VIEWS
        }
        core_env = {
            "FLOWBRIDGE_CORE_DATABASE_URL": _driver_db_url(
                OWNERS["core"], password("core"), names["core"], "asyncpg"
            )
            + "?sslmode=require",
            "FLOWBRIDGE_OIDC_ISSUER": identity["issuer"],
            "FLOWBRIDGE_OIDC_AUDIENCE": identity["client_id"],
            "FLOWBRIDGE_OIDC_INTROSPECTION_URL": (
                f"{keycloak_internal}/realms/{identity['realm']}/protocol/openid-connect/token/introspect"
            ),
            "FLOWBRIDGE_OIDC_CLIENT_ID": identity["client_id"],
            "FLOWBRIDGE_OIDC_CLIENT_SECRET": env[identity["client_secret_env"]],
            "FLOWBRIDGE_OIDC_ALLOWED_CLIENTS": json.dumps(allowed_clients),
            "FLOWBRIDGE_OIDC_SERVICE_CLIENTS": json.dumps(service_clients),
            "FLOWBRIDGE_OIDC_MFA_CLIENTS": "[]",
            "FLOWBRIDGE_HASURA_ENDPOINT": services["hasura"]["internal_url"],
            "FLOWBRIDGE_HASURA_ADMIN_SECRET": env[config["secrets"]["hasura_admin_secret_env"]],
            "FLOWBRIDGE_HASURA_ALLOWED_SOURCES": json.dumps(
                [_source_name(domain) for domain in PRODUCT_VIEWS]
            ),
            "FLOWBRIDGE_HASURA_ALLOWED_DATABASE_HOSTS": json.dumps(["postgres"]),
            "FLOWBRIDGE_HASURA_SECRET_BINDINGS": json.dumps(secret_bindings),
            "FLOWBRIDGE_CORE_SOURCE_SCOPES": json.dumps(scopes),
            "FLOWBRIDGE_CORE_SOURCE_VIEW_KEYS": json.dumps(view_keys()),
            "FLOWBRIDGE_CORE_VERIFIED_HASURA_AUTH_PROFILE": "true",
            "FLOWBRIDGE_CORE_HASURA_JWT_SIGNING_KEY": signing_key,
            "FLOWBRIDGE_CORE_HASURA_JWT_ISSUER": "flowbridge-core",
            "FLOWBRIDGE_CORE_HASURA_JWT_AUDIENCE": "flowbridge-data",
            "FLOWBRIDGE_WORKFLOW_ENABLED": "true",
            "FLOWBRIDGE_FLOWABLE_BASE_URL": services["flowable"]["internal_url"],
            "FLOWBRIDGE_FLOWABLE_USERNAME": config["credentials"]["flowable_admin_user"],
            "FLOWBRIDGE_FLOWABLE_PASSWORD": env[config["secrets"]["flowable_admin_password_env"]],
        }
        for domain in PRODUCT_VIEWS:
            core_env[f"{domain.upper()}_READ_DATABASE_URL"] = (
                _db_url(f"{domain}_reader", password(f"{domain}_reader"), names[domain])
                + "?sslmode=require"
            )
        write(
            "core.env",
            _env_text(core_env),
            protected=tuple(core_env),
            allow_new=True,
        )
        common = {
            "image": "flowbridge-core:local",
            "env_file": [{"path": "./core.env", "format": "raw"}],
            "depends_on": {
                **migrated,
                "keycloak": {"condition": "service_healthy"},
                "hasura": {"condition": "service_started"},
                "flowable": {"condition": "service_started"},
            },
            "networks": ["internal"],
            "restart": "unless-stopped",
        }
        compose["services"]["core-api"] = {
            **common,
            "command": [
                "uvicorn",
                "app.core.runtime:create_app",
                "--factory",
                "--host",
                "0.0.0.0",
                "--port",
                "8080",
            ],
            "ports": [f"127.0.0.1:{services['core']['port']}:8080"],
            "healthcheck": {
                "test": [
                    "CMD",
                    "python",
                    "-c",
                    "import urllib.request; "
                    "urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)",
                ],
                "interval": "10s",
                "timeout": "5s",
                "retries": 20,
            },
        }
        compose["services"]["core-worker"] = {
            **common,
            "command": ["python", "-m", "app.core.runtime", "worker"],
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
