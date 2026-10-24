"""Encrypted PostgreSQL backup/restore for managed deployments (requires age)."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from scripts.deployment_cli import (
    SetupError,
    _compose,
    _preflight,
    _private_directory,
    _secure_existing_secret,
    _wait_health,
)
from scripts.deployment_config import DATABASES, _env_file, validate
from scripts.deployment_render import IMAGES, render


def _load(config_path, env_path):
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    env = _env_file(env_path)
    errors = validate(config, env, config_path.parent)
    if errors:
        raise SetupError("; ".join(errors))
    if env_path.exists():
        _secure_existing_secret(env_path)
    return config, env


def _docker_db_command(stack, name, database, *, restore=False):
    command = (
        'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_restore -h 127.0.0.1 '
        '-U "$POSTGRES_USER" --single-transaction --exit-on-error -d "$1"'
        if restore
        else 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -h 127.0.0.1 '
        '-U "$POSTGRES_USER" -Fc -d "$1"'
    )
    return [
        "docker",
        "compose",
        "-p",
        name,
        "-f",
        str(stack / "compose.yaml"),
        "exec",
        "-T",
        "postgres",
        "sh",
        "-ec",
        command,
        "sh",
        database,
    ]


def _pipe(source, destination):
    first = subprocess.Popen(source, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        second = subprocess.Popen(destination, stdin=first.stdout, stderr=subprocess.PIPE)
    except BaseException:
        first.kill()
        first.wait()
        raise
    first.stdout.close()
    second.communicate(timeout=900)
    first.stderr.read(2048)
    first.wait(timeout=15)
    if first.returncode or second.returncode:
        raise SetupError("Encrypted database transfer failed; inspect target, age key and Docker")


def backup(config_path, env_path, output, recipient):
    if shutil.which("age") is None:
        raise SetupError("age encryption tool is required for backup")
    config, _ = _load(config_path, env_path)
    stack = config_path.parent / "infra" / "deployment" / "generated"
    _compose(stack, config["name"], "config", "--quiet")
    if output.exists():
        raise SetupError("Backup destination already exists; refusing to overwrite")
    _private_directory(output)
    try:
        for key in DATABASES:
            db = config["database"]["names"][key]
            _pipe(
                _docker_db_command(stack, config["name"], db),
                ["age", "-r", recipient, "-o", str(output / f"{key}.dump.age")],
            )
        manifest = {
            "format": 1,
            "source_project": config["name"],
            "databases": config["database"]["names"],
            "volumes": config["volumes"],
            "images": IMAGES,
        }
        descriptor = os.open(output / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, sort_keys=True)
    except BaseException:
        # Preserve partial encrypted output for investigation; never delete user data.
        raise


def restore(config_path, env_path, archive, identity, confirmation):
    if shutil.which("age") is None:
        raise SetupError("age encryption tool is required for restore")
    config, env = _load(config_path, env_path)
    if config["environment"] != "development" or confirmation != config["name"]:
        raise SetupError("Restore requires a development target and exact --confirm-target name")
    _secure_existing_secret(identity)
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or manifest.get("images") != IMAGES:
        raise SetupError("Backup format or pinned image versions are incompatible")
    if manifest.get("source_project") == config["name"] or set(
        manifest.get("volumes", {}).values()
    ) & set(config["volumes"].values()):
        raise SetupError("Restore target must use a different project and volumes")
    if set(manifest.get("databases", {})) != set(DATABASES):
        raise SetupError("Backup must contain all seven managed databases")
    for key in DATABASES:
        if not (archive / f"{key}.dump.age").is_file():
            raise SetupError(f"Encrypted {key} database backup is missing")
    stack = config_path.parent / "infra" / "deployment" / "generated"
    _preflight(config, stack)
    _private_directory(stack)
    render(config, env, stack)
    _compose(stack, config["name"], "config", "--quiet")
    for service in ("keycloak", "hasura", "flowable"):
        if _compose(stack, config["name"], "ps", "-q", service):
            raise SetupError("Restore target applications must be stopped")
    _compose(stack, config["name"], "up", "-d", "postgres")
    _wait_health(stack, config["name"], "postgres")
    _compose(stack, config["name"], "run", "--rm", "provision")
    for key in DATABASES:
        db = config["database"]["names"][key]
        count = _compose(
            stack,
            config["name"],
            "exec",
            "-T",
            "postgres",
            "sh",
            "-ec",
            'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -h 127.0.0.1 -U "$POSTGRES_USER" '
            '-At -d "$1" -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n '
            "ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p') AND "
            "n.nspname NOT IN ('pg_catalog','information_schema') AND "
            "n.nspname NOT LIKE 'pg_toast%'\"",
            "sh",
            db,
        )
        if count.strip() != "0":
            raise SetupError(f"Restore target {key} database is not empty")
    for key in DATABASES:
        db = config["database"]["names"][key]
        _pipe(
            ["age", "-d", "-i", str(identity), str(archive / f"{key}.dump.age")],
            _docker_db_command(stack, config["name"], db, restore=True),
        )
    print("PASS: seven encrypted databases restored to confirmed test target")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backup", "restore"))
    parser.add_argument("--config", type=Path, default=Path("deployment.yaml"))
    parser.add_argument("--env", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--recipient")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--confirm-target")
    args = parser.parse_args(argv)
    config_path = args.config.resolve()
    env_path = args.env or config_path.parent / "secrets" / "deployment.env"
    try:
        if args.command == "backup":
            if not args.output or not args.recipient:
                raise SetupError("backup requires --output and --recipient")
            backup(config_path, env_path, args.output.resolve(), args.recipient)
            print("PASS: encrypted database backup created")
        else:
            if not args.archive or not args.identity or not args.confirm_target:
                raise SetupError("restore requires --archive, --identity and --confirm-target")
            restore(
                config_path,
                env_path,
                args.archive.resolve(),
                args.identity.resolve(),
                args.confirm_target,
            )
        return 0
    except (
        SetupError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as exc:
        print(
            f"FAIL: {exc if isinstance(exc, SetupError) else type(exc).__name__}", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
