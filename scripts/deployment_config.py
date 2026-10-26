"""Validate Phase A deployment configuration without contacting Docker or services."""

import argparse
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

SERVICES = ("postgres", "keycloak", "hasura", "flowable", "core")
SECRET_KEYS = {
    "postgres": ("database", "password_env"),
    "keycloak": ("secrets", "keycloak_admin_password_env"),
    "hasura": ("secrets", "hasura_admin_secret_env"),
    "flowable": ("secrets", "flowable_admin_password_env"),
}
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VOLUME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
DEPLOYMENT_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
DB_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DATABASES = ("core", "sales", "warehouse", "logistics", "hasura_metadata", "flowable", "keycloak")
READERS = ("sales_reader", "warehouse_reader", "logistics_reader")
RUNTIME_DB_USERS = {
    "core_owner",
    "sales_owner",
    "warehouse_owner",
    "logistics_owner",
    "hasura_owner",
    "wf_engine",
    "keycloak_owner",
    *READERS,
}


def _dns_name(hostname):
    return bool(hostname and all(DNS_LABEL.fullmatch(label) for label in hostname.split(".")))


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _env_file(path):
    """Read literal KEY=VALUE lines; process env takes precedence."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if separator and ENV_NAME.fullmatch(key.strip()):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    values.update(os.environ)
    return values


def validate(config, env=None, base_dir=None):
    """Return safe diagnostic strings; never include secret values."""
    env = os.environ if env is None else env
    base_dir = Path.cwd() if base_dir is None else Path(base_dir)
    errors = []
    if not isinstance(config, dict):
        return ["deployment config must be a mapping"]

    for field in ("name", "domain"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            errors.append(f"{field} is required")
    if isinstance(config.get("name"), str) and not DEPLOYMENT_NAME.fullmatch(config["name"]):
        errors.append("name must use lowercase letters, digits, and hyphens")
    environment = config.get("environment")
    if environment not in ("development", "staging", "production"):
        errors.append("environment must be development, staging, or production")
    domain = config.get("domain", "")
    if (
        isinstance(domain, str)
        and domain
        and ("." not in domain or ":" in domain or "/" in domain or domain.endswith("."))
    ):
        errors.append("domain must be a DNS name")

    services = _mapping(config.get("services"))
    ports = {}
    enabled = {}
    for name in SERVICES:
        service = _mapping(services.get(name))
        active = service.get("enabled")
        if not isinstance(active, bool):
            errors.append(f"services.{name}.enabled must be a boolean")
            continue
        enabled[name] = active
        if not active:
            continue
        port = service.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            errors.append(f"services.{name}.port must be between 1 and 65535")
        elif port in ports:
            errors.append(f"services.{name}.port conflicts with services.{ports[port]}.port")
        else:
            ports[port] = name
        if name == "postgres":
            continue
        if not enabled.get("postgres"):
            errors.append(f"services.{name} requires services.postgres")
        if name == "hasura" and not enabled.get("keycloak"):
            errors.append("services.hasura requires services.keycloak for JWT keys")
        if name == "core" and any(
            not enabled.get(dependency) for dependency in ("keycloak", "hasura", "flowable")
        ):
            errors.append(
                "services.core requires services.keycloak, services.hasura and services.flowable"
            )
        external = service.get("external", False)
        if not isinstance(external, bool):
            errors.append(f"services.{name}.external must be a boolean")
        for kind in ("public_url", "internal_url"):
            value = service.get(kind)
            try:
                parsed = urlparse(value) if isinstance(value, str) else None
                hostname = parsed.hostname if parsed else None
                parsed_port = parsed.port if parsed else None
            except ValueError:
                parsed = None
                hostname = None
                parsed_port = None
            if (
                not parsed
                or parsed.scheme not in ("http", "https")
                or not _dns_name(hostname)
                or any(char.isspace() for char in value)
            ):
                errors.append(f"services.{name}.{kind} must be an HTTP(S) URL")
                continue
            if parsed_port is not None and not 1 <= parsed_port <= 65535:
                errors.append(f"services.{name}.{kind} has an invalid port")
            if kind == "internal_url" and not external:
                expected_hostname = "core-api" if name == "core" else name
                if hostname != expected_hostname:
                    errors.append(
                        f"services.{name}.internal_url must use Docker service DNS "
                        f"'{expected_hostname}'"
                    )
                if parsed_port is None:
                    errors.append(f"services.{name}.internal_url needs a container port")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                errors.append(f"services.{name}.{kind} must not contain credentials or query")
            if kind == "public_url" and isinstance(domain, str) and domain:
                if hostname != domain and not hostname.endswith("." + domain):
                    errors.append(f"services.{name}.public_url must use domain")
            if name == "flowable" and kind == "internal_url" and not external:
                container_port = service.get("container_port", parsed_port)
                if type(container_port) is not int or not 1 <= container_port <= 65535:
                    errors.append("services.flowable.container_port must be between 1 and 65535")
                elif parsed_port != container_port:
                    errors.append("services.flowable.internal_url must use container_port")
                if parsed.path.rstrip("/") != "/flowable-rest/service":
                    errors.append("services.flowable.internal_url must use /flowable-rest/service")
        if service.get("public_url") == service.get("internal_url"):
            errors.append(f"services.{name} public and internal URLs must differ")
        public_url = service.get("public_url")
        try:
            public_scheme = urlparse(public_url).scheme if isinstance(public_url, str) else None
        except ValueError:
            public_scheme = None
        if environment == "production" and public_scheme != "https":
            errors.append(f"services.{name}.public_url must use HTTPS in production")

    if enabled.get("flowable"):
        flowable = _mapping(services.get("flowable"))
        policy = flowable.get("schema_policy")
        if policy not in ("update", "manual"):
            errors.append("services.flowable.schema_policy must be update or manual")
        elif policy == "update" and environment != "development":
            errors.append("services.flowable.schema_policy=update is development-only")
        elif policy == "manual" and flowable.get("schema_prepared") is not True:
            errors.append("services.flowable.schema_prepared=true required for manual schema")

    database = _mapping(config.get("database"))
    if enabled.get("postgres"):
        names = database.get("names")
        if names is not None and not isinstance(names, dict):
            errors.append("database.names must be a mapping")
        elif isinstance(names, dict):
            if names.keys() - set(DATABASES):
                errors.append("database.names contains unknown keys")
            seen_names = set()
            for field in DATABASES:
                value = names.get(field)
                if not isinstance(value, str) or not DB_IDENTIFIER.fullmatch(value):
                    errors.append(f"database.names.{field} must be a PostgreSQL identifier")
                elif value in seen_names:
                    errors.append(f"database.names.{field} must be unique")
                else:
                    seen_names.add(value)
        elif not isinstance(database.get("name"), str) or not DB_IDENTIFIER.fullmatch(
            database["name"]
        ):
            errors.append("database.name must be a PostgreSQL identifier")
        if not isinstance(database.get("user"), str) or not DB_IDENTIFIER.fullmatch(
            database["user"]
        ):
            errors.append("database.user must be a PostgreSQL identifier")
        elif database["user"] in RUNTIME_DB_USERS:
            errors.append("database.user must not be a runtime role")

    identity = _mapping(config.get("identity"))
    if enabled.get("keycloak"):
        for field in ("realm", "client_id"):
            if not isinstance(identity.get(field), str) or not identity[field].strip():
                errors.append(f"identity.{field} is required")
        issuer = identity.get("issuer")
        public_url = _mapping(services.get("keycloak")).get("public_url")
        if not isinstance(issuer, str) or issuer != (
            f"{public_url.rstrip('/')}/realms/{identity.get('realm')}"
            if isinstance(public_url, str)
            else None
        ):
            errors.append("identity.issuer must match the public Keycloak realm URL")
        attribute = identity.get("hasura_default_role_attribute", "hasura_default_role")
        if not isinstance(attribute, str) or not ENV_NAME.fullmatch(attribute):
            errors.append("identity.hasura_default_role_attribute must be an attribute name")
        secret_fields = [("identity", "client_secret_env")]
    else:
        secret_fields = []

    secrets = _mapping(config.get("secrets"))
    credentials = _mapping(config.get("credentials"))
    for name, field in (
        ("keycloak", "keycloak_admin_user"),
        ("flowable", "flowable_admin_user"),
    ):
        if enabled.get(name) and (
            not isinstance(credentials.get(field), str) or not credentials[field].strip()
        ):
            errors.append(f"credentials.{field} is required")
    for name, (section, field) in SECRET_KEYS.items():
        if enabled.get(name):
            secret_fields.append((section, field))
    role_password_env = _mapping(database.get("role_password_env"))
    if database.get("names") is not None and enabled.get("postgres"):
        used_role_env = set()
        for role in (*DATABASES, *READERS):
            if role == "keycloak" and not enabled.get("keycloak"):
                continue
            if role == "flowable" and not enabled.get("flowable"):
                continue
            env_name = role_password_env.get(role)
            if not isinstance(env_name, str) or not ENV_NAME.fullmatch(env_name):
                errors.append(
                    f"database.role_password_env.{role} must name an environment variable"
                )
            elif not env.get(env_name):
                errors.append(
                    f"database.role_password_env.{role} references an unset or empty variable"
                )
            elif env_name == database.get("password_env") or env_name in used_role_env:
                errors.append(f"database.role_password_env.{role} must use a separate variable")
            else:
                used_role_env.add(env_name)
    for section, field in secret_fields:
        source = {"database": database, "identity": identity, "secrets": secrets}[section]
        env_name = source.get(field)
        if not isinstance(env_name, str) or not ENV_NAME.fullmatch(env_name):
            errors.append(f"{section}.{field} must name an environment variable")
        elif not env.get(env_name):
            errors.append(f"{section}.{field} references an unset or empty environment variable")

    tls = _mapping(config.get("tls"))
    if not isinstance(tls.get("enabled"), bool):
        errors.append("tls.enabled must be a boolean")
    if environment == "production" and tls.get("enabled") is not True:
        errors.append("tls.enabled is required in production")
    if tls.get("enabled"):
        termination = tls.get("termination")
        if termination not in ("external", "files"):
            errors.append("tls.termination must be external or files")
        if termination == "files":
            for field in ("cert_file", "key_file"):
                file = tls.get(field)
                if not isinstance(file, str) or not file.strip() or not (base_dir / file).is_file():
                    errors.append(f"tls.{field} must point to an existing file")

    volumes = _mapping(config.get("volumes"))
    seen_volumes = set()
    for name in ("postgres", "keycloak", "flowable"):
        if not enabled.get(name):
            continue
        volume = volumes.get(name)
        if not isinstance(volume, str) or not VOLUME_NAME.fullmatch(volume):
            errors.append(f"volumes.{name} must be a Docker volume name")
        elif volume in seen_volumes:
            errors.append(f"volumes.{name} must be unique")
        else:
            seen_volumes.add(volume)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("deployment.yaml"))
    parser.add_argument("--env", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
        errors = validate(config, _env_file(args.env), args.config.parent)
    except (OSError, yaml.YAMLError) as exc:
        # YAML parser details may include a secret accidentally pasted into config.
        print(f"FAIL: cannot read or parse {args.config} ({type(exc).__name__})")
        return 1
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1
    print("PASS: deployment configuration is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
