"""Interactive deployment setup; run with ``python -m scripts.deployment_cli``."""

import argparse
import json
import os
import re
import secrets as random_secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from infra.deployment.keycloak_bootstrap import bootstrap
from infra.deployment.readiness import check as check_readiness

from scripts.deployment_config import DATABASES, READERS, SERVICES, _env_file, validate
from scripts.deployment_render import render

EXAMPLE = (
    Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    / "deployment.example.yaml"
)
MIN_MEMORY = 4 * 1024**3
MIN_DISK = 5 * 1024**3
VERSION = "0.2.0.dev0"


def _default_config_path():
    if not getattr(sys, "frozen", False):
        return Path("deployment.yaml")
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return root / "FlowBridge" / "deployments" / "default" / "deployment.yaml"


class SetupError(Exception):
    """A safe, user-facing deployment failure."""


def _record_state(config_path, config, status):
    """Persist only non-secret deployment identity and lifecycle status."""
    state_path = config_path.parent / "deployment.state.json"
    temporary = state_path.with_name(f"deployment.state.{random_secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "deployment_id": config["name"],
                    "version": VERSION,
                    "config_location": str(config_path),
                    "status": status,
                },
                stream,
            )
        os.replace(temporary, state_path)
    finally:
        temporary.unlink(missing_ok=True)


def _redact(message, secret_values=()):
    for value in secret_values:
        if isinstance(value, str) and len(value) >= 4:
            message = message.replace(value, "[redacted]")
    message = re.sub(
        r"(?i)(password|secret|token|authorization)([=: ]+)(\S+)", r"\1\2[redacted]", message
    )
    message = re.sub(r"(?i)\b(postgres(?:ql)?|https?)://[^\s]+", "[redacted URL]", message)
    return message


def _docker_reason(stderr):
    lower = stderr.lower()
    if any(
        word in lower for word in ("port is already allocated", "address already in use", "bind:")
    ):
        return "port conflict", "Choose another host port; no process was stopped"
    if any(
        word in lower
        for word in (
            "cannot connect to the docker daemon",
            "docker daemon is not running",
            "error during connect",
        )
    ):
        return "Docker unavailable", "Start Docker Desktop/Engine, then retry"
    if any(
        word in lower
        for word in (
            "pull access denied",
            "failed to pull",
            "manifest unknown",
            "not found: manifest",
            "failed to resolve reference",
        )
    ):
        return "image pull", "Check image access and network connectivity"
    if any(word in lower for word in ("permission denied", "access is denied")):
        return "permissions", "Check Docker and deployment-directory permissions"
    if any(word in lower for word in ("psql:", "database system", "role ", "database ")):
        return "database", "Inspect the private setup log and database provisioning"
    if any(word in lower for word in ("unhealthy", "healthcheck", "timed out")):
        return "health/readiness", "Inspect the affected service's private logs"
    return "Docker command", "Inspect the private setup log"


def _port_owner(port, project):
    """Return a safe explanation, or None when the port is free/owned by this project."""
    result = subprocess.run(
        ["docker", "ps", "--filter", f"publish={port}", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode:
        raise SetupError("Docker unavailable; start Docker Desktop/Engine and retry")
    for line in result.stdout.splitlines():
        try:
            container = json.loads(line)
        except ValueError:
            continue
        if not re.search(
            rf"(?:127\.0\.0\.1|0\.0\.0\.0|\[::\]):{port}->", container.get("Ports", "")
        ):
            continue
        labels = container.get("Labels", "")
        if f"com.docker.compose.project={project}" in labels:
            return None
        return f"Docker container {container.get('Names', 'unknown')}"
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return "host process or container"
    return None


def _port_conflicts(config):
    conflicts = []
    for service in SERVICES:
        item = config["services"][service]
        if not item["enabled"]:
            continue
        port = item["port"]
        owner = _port_owner(port, config["name"])
        if owner:
            conflicts.append((service, port, owner))
    return conflicts


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _resolve_ports(config):
    ui = _ui()
    for _ in range(12):
        conflicts = _port_conflicts(config)
        if not conflicts:
            return
        service, port, owner = conflicts[0]
        print(f"Port {port} for {service} is occupied by {owner}")
        choice = _ask(
            ui.select("Choose a port", choices=["Suggest free port", "Enter port", "Cancel"])
        )
        if choice == "Cancel":
            raise KeyboardInterrupt
        replacement = (
            _free_port()
            if choice == "Suggest free port"
            else _port(_ask(ui.text(f"New {service} port")), service)
        )
        config["services"][service]["port"] = replacement
        if config["environment"] == "development" and service != "postgres":
            old = config["services"][service]["public_url"]
            parsed = urlsplit(old)
            config["services"][service]["public_url"] = (
                f"{parsed.scheme}://{parsed.hostname}:{replacement}"
            )
            if service == "keycloak":
                config["identity"]["issuer"] = (
                    config["services"][service]["public_url"]
                    + "/realms/"
                    + config["identity"]["realm"]
                )
    raise SetupError("Port conflicts remain after repeated changes")


def _ui():
    try:
        import questionary
    except ImportError as exc:
        raise SetupError("Install project dependencies to use the interactive wizard") from exc
    return questionary


def _ask(question):
    answer = question.ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer


def _port(value, service):
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise SetupError(f"{service} port must be between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise SetupError(f"{service} port must be between 1 and 65535")
    return port


def _safe_url(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = f":{parsed.port}" if parsed.port else ""
        if parsed.scheme in ("http", "https") and host:
            return f"{parsed.scheme}://{host}{port}"
    except (TypeError, ValueError):
        pass
    return "[invalid URL]"


def _env_names(config):
    enabled = config["services"]
    names = [config["database"]["password_env"]]
    names += [
        config["database"]["role_password_env"][role]
        for role in (*DATABASES, *READERS)
        if role not in ("flowable", "keycloak") or enabled[role]["enabled"]
    ]
    if enabled["keycloak"]["enabled"]:
        names += [
            config["identity"]["client_secret_env"],
            config["secrets"]["keycloak_admin_password_env"],
        ]
    if enabled["hasura"]["enabled"]:
        names.append(config["secrets"]["hasura_admin_secret_env"])
    if enabled["flowable"]["enabled"]:
        names.append(config["secrets"]["flowable_admin_password_env"])
    return names


def _private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "nt":
        account = os.environ.get("USERNAME")
        if not account:
            raise SetupError("Cannot determine Windows account for secret ACL")
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(OI)(CI)F"],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise SetupError("Cannot secure secret directory ACL")
    elif path.stat().st_mode & 0o077:
        raise SetupError("Secret directory permissions are too broad")


def _save_secrets(path, values):
    if path.exists():
        raise SetupError("Secret file already exists; refusing to overwrite")
    _private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for name, value in values.items():
                stream.write(f"{name}={value}\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _secure_existing_secret(path):
    if not path.is_file():
        raise SetupError("Secret file missing; configure it before setup")
    if os.name == "nt":
        account = os.environ.get("USERNAME")
        if not account:
            raise SetupError("Cannot determine Windows account for secret ACL")
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:F"],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise SetupError("Cannot secure secret file ACL")
    elif stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SetupError("Secret file permissions must be 0600 or stricter")


def wizard(existing=None, credential_values=None):
    ui = _ui()
    config = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")) if existing is None else existing
    name = _ask(ui.text("Deployment name", default=config["name"] if existing else "flowbridge"))
    environment = _ask(
        ui.select(
            "Environment",
            choices=["development", "staging", "production"],
            default=config["environment"],
        )
    )
    mode = _ask(ui.select("Setup mode", choices=["Quick", "Advanced"]))
    if existing is None and mode == "Quick" and name != "flowbridge":
        config["volumes"] = {
            key: f"{name.replace('-', '_')}_{key}" for key in config["volumes"]
        }
    selected = _ask(
        ui.checkbox(
            "Services (Space to toggle)",
            choices=[
                ui.Choice(service, checked=config["services"][service]["enabled"])
                for service in SERVICES
            ],
        )
    )
    if "postgres" not in selected or ("hasura" in selected and "keycloak" not in selected):
        raise SetupError("PostgreSQL is required; Hasura also requires Keycloak")
    domain = _ask(
        ui.text("Domain", default="127.0.0.1" if environment == "development" else config["domain"])
    )
    config.update(name=name, environment=environment, domain=domain)
    scheme = "http" if environment == "development" else "https"
    for service in SERVICES:
        item = config["services"][service]
        item["enabled"] = service in selected
        if service == "postgres" or service not in selected:
            continue
        host = f"{service}.{domain}" if environment != "development" else domain
        item["public_url"] = f"{scheme}://{host}"
        if environment == "development":
            item["public_url"] += f":{item['port']}"
    if environment == "development":
        config["services"]["keycloak"]["public_url"] = (
            f"http://{domain}:{config['services']['keycloak']['port']}"
        )
    config["identity"]["issuer"] = (
        config["services"]["keycloak"]["public_url"] + "/realms/" + config["identity"]["realm"]
    )
    config["tls"]["enabled"] = environment != "development"
    config["services"]["flowable"]["schema_policy"] = (
        "update" if environment == "development" else "manual"
    )
    if environment != "development" and "flowable" in selected:
        config["services"]["flowable"]["schema_prepared"] = _ask(
            ui.confirm("Flowable schema already prepared?", default=False)
        )
    if mode == "Advanced":
        for service in selected:
            config["services"][service]["port"] = _port(
                _ask(
                    ui.text(
                        f"{service} host port", default=str(config["services"][service]["port"])
                    )
                ),
                service,
            )
        for service in selected:
            if service != "postgres":
                config["services"][service]["public_url"] = _ask(
                    ui.text(
                        f"{service} public URL", default=config["services"][service]["public_url"]
                    )
                )
                config["services"][service]["internal_url"] = _ask(
                    ui.text(
                        f"{service} internal Docker URL",
                        default=config["services"][service]["internal_url"],
                    )
                )
        for database in DATABASES:
            config["database"]["names"][database] = _ask(
                ui.text(f"{database} database", default=config["database"]["names"][database])
            )
        for service in ("postgres", "keycloak", "flowable"):
            if service in selected:
                config["volumes"][service] = _ask(
                    ui.text(f"{service} volume", default=f"{name}_{service}")
                )
        config["tls"]["enabled"] = _ask(
            ui.confirm("TLS enabled behind external proxy?", default=config["tls"]["enabled"])
        )
        config["identity"]["realm"] = (
            _ask(ui.text("Keycloak realm", default=config["identity"]["realm"]))
            if "keycloak" in selected
            else config["identity"]["realm"]
        )
        config["identity"]["issuer"] = (
            config["services"]["keycloak"]["public_url"].rstrip("/")
            + "/realms/"
            + config["identity"]["realm"]
        )
    else:
        for service in ("postgres", "keycloak", "flowable"):
            config["volumes"][service] = f"{name}_{service}"
    if mode == "Advanced" and existing is None and credential_values is not None:
        if (
            _ask(ui.select("Credentials", choices=["Generate strong secrets", "Enter manually"]))
            == "Enter manually"
        ):
            for env_name in _env_names(config):
                value = _ask(ui.password(f"{env_name} (hidden)"))
                if not value or value != value.strip() or "\n" in value or "\r" in value:
                    raise SetupError(f"{env_name} must be a nonempty single-line secret")
                credential_values[env_name] = value
    if environment != "development" and not _ask(
        ui.confirm("External TLS proxy and public DNS are already configured?", default=False)
    ):
        raise SetupError("Production/staging requires external TLS and DNS")
    return config


def _run(command, *, timeout=180, step=None, log_path=None, secret_values=()):
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "command unavailable"
        raise SetupError(f"{step or command[0]}: {reason}; check Docker and retry") from exc
    if result.returncode:
        category, remedy = _docker_reason(result.stderr)
        sanitized = _redact(result.stderr, secret_values)
        if log_path is not None:
            _private_directory(log_path.parent)
            descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                stream.write(f"{step or command[0]}: {category}\n")
                stream.write("\n".join(sanitized.splitlines()[-5:])[:1500] + "\n")
        # Keep stderr in a private log only; its free-form text may include secrets.
        raise SetupError(f"{step or command[0]}: {category}. {remedy}")
    return result.stdout.strip()


def _compose(stack, name, *args):
    values = []
    if stack.is_dir():
        for path in stack.glob("*.env"):
            values.extend(
                line.split("=", 1)[1]
                for line in path.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
    return _run(
        ["docker", "compose", "-p", name, "-f", str(stack / "compose.yaml"), *args],
        timeout=300,
        step="Compose " + " ".join(args[:2]),
        log_path=stack / "setup.log",
        secret_values=values,
    )


def _preflight(config, stack):
    if shutil.which("docker") is None:
        raise SetupError("Docker unavailable; install/start Docker Desktop or Engine")
    _run(["docker", "compose", "version"], step="Compose preflight")
    try:
        info = json.loads(
            _run(["docker", "info", "--format", "{{json .}}"], step="Docker preflight")
        )
    except ValueError as exc:
        raise SetupError("Docker preflight: cannot read daemon information") from exc
    if info.get("MemTotal", 0) < MIN_MEMORY:
        raise SetupError("Docker memory below 4 GiB; increase Docker allocation")
    directory = stack
    while not directory.exists():
        directory = directory.parent
    if shutil.disk_usage(directory).free < MIN_DISK:
        raise SetupError("Less than 5 GiB free on deployment filesystem")
    conflicts = _port_conflicts(config)
    if conflicts:
        service, port, owner = conflicts[0]
        raise SetupError(f"Port {port} for {service} is occupied by {owner}; choose another port")


def _wait_health(stack, name, service, timeout=180):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        container = _compose(stack, name, "ps", "-q", service)
        if container:
            state = _run(["docker", "inspect", "--format", "{{.State.Health.Status}}", container])
            if state == "healthy":
                return
            if state == "unhealthy":
                raise SetupError(f"{service} unhealthy; inspect its container logs")
        time.sleep(3)
    raise SetupError(f"{service} readiness timed out")


def _setup(config, env, stack, *, dry_run=False, secret_path=None):
    errors = validate(config, env, stack)
    if errors:
        raise SetupError("; ".join(errors))
    if dry_run:
        print("PASS: configuration valid; deployment dry run (no Docker changes)")
        return
    if secret_path is not None and secret_path.exists():
        _secure_existing_secret(secret_path)
    print("[0/6] Preflight Docker, resources and ports")
    _preflight(config, stack)
    print("[1/6] Rendering private stack")
    _private_directory(stack)
    render(config, env, stack)
    _compose(stack, config["name"], "config", "--quiet")
    print("[2/6] Starting PostgreSQL")
    _compose(stack, config["name"], "up", "-d", "postgres")
    _wait_health(stack, config["name"], "postgres")
    print("[3/6] Provisioning databases")
    _compose(stack, config["name"], "run", "--rm", "provision")
    print("[4/6] Starting services")
    if config["services"]["keycloak"]["enabled"]:
        _compose(stack, config["name"], "up", "-d", "--no-deps", "keycloak")
        _wait_health(stack, config["name"], "keycloak", timeout=240)
        print("[5/6] Bootstrapping Keycloak")
        bootstrap(
            config, stack, base_url=f"http://127.0.0.1:{config['services']['keycloak']['port']}"
        )
    remaining = [
        service for service in ("hasura", "flowable") if config["services"][service]["enabled"]
    ]
    if remaining:
        _compose(stack, config["name"], "up", "-d", "--no-deps", *remaining)
    print("[6/6] Verifying application readiness")
    until = time.monotonic() + 180
    while time.monotonic() < until:
        failures = check_readiness(stack)
        if not failures:
            break
        time.sleep(5)
    else:
        raise SetupError("Application readiness timed out; check service logs")
    print(
        "DONE: "
        + ", ".join(
            f"{service}={config['services'][service]['public_url']}"
            for service in ("keycloak", "hasura", "flowable")
            if config["services"][service]["enabled"]
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="flowbridge-setup")
    parser.add_argument(
        "command", nargs="?", default="setup",
        choices=("init", "setup", "validate", "status", "start", "stop", "doctor"),
    )
    parser.add_argument("--config", type=Path, default=_default_config_path())
    parser.add_argument("--env", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args(argv)
    config_path = args.config.resolve()
    env_path = (args.env or config_path.parent / "secrets" / "deployment.env").resolve()
    if args.env is None and not env_path.exists() and (config_path.parent / ".env").exists():
        env_path = config_path.parent / ".env"
    stack = config_path.parent / "infra" / "deployment" / "generated"
    try:
        existing = config_path.exists()
        new_seed = None
        requested_id = None
        if args.command in ("init", "setup") and not args.non_interactive:
            if existing:
                choice = _ask(
                    _ui().select(
                        "Configuration exists",
                        choices=["Use existing", "Repair", "New", "Edit", "Cancel"],
                    )
                )
                if choice == "Cancel":
                    return 130
                if choice == "New":
                    if args.env is not None:
                        raise SetupError("New deployment needs its own secrets path; omit --env")
                    deployment_id = _ask(_ui().text("New deployment ID"))
                    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", deployment_id):
                        raise SetupError(
                            "Deployment ID must be lowercase letters, digits or hyphens"
                        )
                    base = (
                        config_path.parent.parent
                        if getattr(sys, "frozen", False)
                        else config_path.parent / "deployments"
                    )
                    config_path = base / deployment_id / "deployment.yaml"
                    if config_path.exists():
                        raise SetupError("New deployment already exists; choose another ID")
                    env_path = config_path.parent / "secrets" / "deployment.env"
                    stack = config_path.parent / "infra" / "deployment" / "generated"
                    new_seed = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
                    new_seed["name"] = deployment_id
                    requested_id = deployment_id
                    new_seed["volumes"] = {
                        key: f"{deployment_id.replace('-', '_')}_{key}"
                        for key in new_seed["volumes"]
                    }
                    existing = False
                    edit = True
                else:
                    edit = choice == "Edit"
            else:
                edit = True
            config = (
                yaml.safe_load(config_path.read_text(encoding="utf-8")) if existing else new_seed
            )
            if edit:
                supplied = {}
                config = wizard(config, supplied)
                if requested_id is not None and config["name"] != requested_id:
                    raise SetupError("New deployment name must match its deployment ID")
                if args.command == "setup" and not args.dry_run:
                    try:
                        _resolve_ports(config)
                    except SetupError as exc:
                        if "Docker unavailable" not in str(exc):
                            raise
                        print("Docker unavailable; ports will be checked during setup")
                review = {
                    "name": config["name"],
                    "environment": config["environment"],
                    "domain": config["domain"],
                    "services": {
                        name: {
                            key: _safe_url(item[key]) if key.endswith("_url") else item[key]
                            for key in ("enabled", "port", "public_url", "internal_url")
                            if key in item
                        }
                        for name, item in config["services"].items()
                    },
                    "database_names": config["database"]["names"],
                    "tls_enabled": config["tls"]["enabled"],
                    "volumes": config["volumes"],
                    "secrets": "[hidden]",
                }
                print("Review (secret values hidden):")
                print(yaml.safe_dump(review, sort_keys=False))
                if not _ask(_ui().confirm("Save configuration and continue?", default=False)):
                    return 130
                env = _env_file(env_path) if existing else {}
                missing = [name for name in _env_names(config) if not env.get(name)]
                if existing and missing:
                    raise SetupError("Existing deployment has missing secrets; refusing to rotate")
                if not existing:
                    env = {
                        name: supplied.get(name) or random_secrets.token_urlsafe(32)
                        for name in missing
                    }
                errors = validate(config, env, config_path.parent)
                if errors:
                    raise SetupError("; ".join(errors))
                if not args.dry_run:
                    if not existing:
                        _save_secrets(env_path, env)
                    config_path.write_text(
                        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
                    )
            elif args.command == "init":
                print("Existing configuration preserved")
                return 0
        else:
            if not existing:
                raise SetupError("Configuration missing; run interactive init first")
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        env = (
            env
            if args.command in ("init", "setup") and not existing and args.dry_run
            else _env_file(env_path)
        )
        if args.command == "validate" or args.command == "init":
            errors = validate(config, env, config_path.parent)
            if errors:
                raise SetupError("; ".join(errors))
            print("PASS: deployment configuration valid")
        elif args.command == "setup":
            _setup(config, env, stack, dry_run=args.dry_run, secret_path=env_path)
            if not args.dry_run:
                _record_state(config_path, config, "running")
        elif args.command == "doctor":
            errors = validate(config, env, config_path.parent)
            if errors:
                raise SetupError("; ".join(errors))
            _preflight(config, stack)
            if (stack / "compose.yaml").exists():
                _compose(stack, config["name"], "config", "--quiet")
                failures = check_readiness(stack)
                if failures:
                    raise SetupError("Services not ready: " + ", ".join(failures))
            print("PASS: Docker, configuration, ports and deployed readiness")
        elif args.command == "status":
            print(_compose(stack, config["name"], "ps") if not args.dry_run else "DRY RUN: status")
        else:
            if not args.dry_run:
                _compose(
                    stack, config["name"], *("up", "-d") if args.command == "start" else ("stop",)
                )
                _record_state(
                    config_path, config, "running" if args.command == "start" else "stopped"
                )
            print(f"{'DRY RUN: ' if args.dry_run else ''}{args.command} complete")
        return 0
    except KeyboardInterrupt:
        print("Cancelled; no resources were deleted", file=sys.stderr)
        return 130
    except (SetupError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        message = str(exc) if isinstance(exc, SetupError) else type(exc).__name__
        print(f"FAIL: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
