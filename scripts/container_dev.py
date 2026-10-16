# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Container entrypoints call the existing app, migration reader and test fixture tools.
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen


class ContainerError(Exception):
    pass


ROOT = Path(__file__).resolve().parents[1]
STATE = Path("/state")
CONFIG = Path("/config")


def profile() -> dict[str, object]:
    api, iam = (
        int(os.environ.get("FLOWBRIDGE_DEV_API_PORT", "18082")),
        int(os.environ.get("FLOWBRIDGE_DEV_IAM_PORT", "19444")),
    )
    if (
        not all(1024 <= port <= 65535 for port in (api, iam))
        or len({api, iam, 8761, 8762, 9000}) != 5
    ):
        raise ContainerError("Choose distinct unprivileged API/IAM ports outside 8761/8762/9000")
    return {
        "version": 1,
        "api": api,
        "iam": iam,
        "lifespan": 20 if os.environ.get("FLOWBRIDGE_DEV_TEST_MODE") == "true" else 180,
    }


def prepare_state(
    state: Path = STATE, config: Path = CONFIG, source: Path = Path("/run/secrets/database-source")
) -> None:
    from dotenv import dotenv_values
    from prepare_identity import configure_core, prepare

    metadata = profile()
    marker = state / "dev-profile.json"
    if marker.exists():
        if json.loads(marker.read_text("utf-8")) != metadata:
            raise ContainerError("Existing IAM state belongs to another profile; use a new project")
        for name in (
            "operator.json",
            "runtime.json",
            "realm.json",
            "bootstrap-password",
            "import/flowbridge-realm.json",
        ):
            if not (state / name).is_file():
                raise ContainerError("Incomplete IAM state; inspect it before retrying")
    else:
        # Do not silently rotate secrets left by a failed/partial preparation.
        if state.exists() and any(state.iterdir()):
            raise ContainerError("Unrecognized nonempty IAM state; use a new project")
        prepare(state, int(metadata["iam"]), f"http://127.0.0.1:{metadata['api']}", True)
        configure_core(state, "local-dev", "supplier-assistant", int(metadata["lifespan"]))
        (state / "import").mkdir()
        shutil.copyfile(state / "realm.json", state / "import/flowbridge-realm.json")
        operator = json.loads((state / "operator.json").read_text("utf-8"))
        (state / "bootstrap-password").write_text(operator["bootstrap_password"], "utf-8")
        marker.write_text(json.dumps(metadata) + "\n", "utf-8")
    # Only DB settings enter the separate backend volume, never the IAM/browser state volume.
    database = {
        key: value
        for key, value in dotenv_values(source).items()
        if key.startswith("FLOWBRIDGE_DATABASE_") and value is not None
    }
    config.mkdir(parents=True, exist_ok=True)
    existing_database = config / "database.json"
    if (state / "fixture.json").exists() and (
        not existing_database.is_file()
        or json.loads(existing_database.read_text("utf-8")) != database
    ):
        raise ContainerError(
            "Pending fixture uses another DB configuration; recover before changing it"
        )
    (config / "database.json").write_text(json.dumps(database) + "\n", "utf-8")
    # A writable group directory lets the isolated browser store test OTP material.
    # IAM/API receive read-only mounts; only preparation/test helpers write these volumes.
    if hasattr(os, "chown"):
        for directory in (state, config):
            for path in [directory, *directory.rglob("*")]:
                os.chown(path, 10001, 0)
                path.chmod(0o770 if path.is_dir() else 0o640)
    print("Prepared/reused isolated local IAM state; backend DB config stored separately.")


def load_database(config: Path = CONFIG) -> None:
    database = json.loads((config / "database.json").read_text("utf-8"))
    if not isinstance(database, dict) or any(
        not key.startswith("FLOWBRIDGE_DATABASE_") or not isinstance(value, str)
        for key, value in database.items()
    ):
        raise ContainerError("Invalid generated database configuration")
    os.environ.update(database)


async def check_database(require_head: bool) -> None:
    import sqlalchemy as sa
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from flowbridge.db import create_engine
    from flowbridge.settings import Settings

    settings = Settings(_env_file=None)
    if settings.database_url is None:
        raise ContainerError(
            "Configure a development DB with --env-file; no migration or seed is automatic"
        )
    engine = create_engine(settings, use_null_pool=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.text("SET TRANSACTION READ ONLY"))
            await connection.execute(sa.text("SELECT 1"))
            if require_head:
                heads = set(
                    ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads()
                )
                current = set(
                    (
                        await connection.execute(
                            sa.text("SELECT version_num FROM public.alembic_version")
                        )
                    ).scalars()
                )
                if current != heads:
                    raise ContainerError(
                        "DB revision differs from source; reconcile migrations before tests"
                    )
    finally:
        await engine.dispose()
    print("PASS: read-only DB connectivity" + (" and migration head" if require_head else ""))


def run_tool(action: str) -> int:
    fixture = [sys.executable, "tests/e2e/identity_fixture.py", "--identity-dir", str(STATE)]
    if action == "cleanup":
        if not (STATE / "fixture.json").exists():
            print("No committed fixture manifest; no cleanup SQL executed.")
            return 0
        return subprocess.run([*fixture, "--cleanup"], cwd=ROOT, check=False).returncode
    if action == "fixture":
        return subprocess.run(fixture, cwd=ROOT, check=False).returncode
    test = {
        "sql-identity": "tests/integration/test_identity_accounts_database.py",
        "sql-business": "tests/integration/test_supplier_database.py",
        "iam-sql": "tests/integration/test_identity_core_database.py",
    }[action]
    os.environ["FLOWBRIDGE_SSO_IDENTITY_DIR"] = str(STATE)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            test,
            "-m",
            "integration",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            "/tmp/pytest",
        ],
        cwd=ROOT,
        check=False,
    ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal local-container helper")
    parser.add_argument(
        "action",
        choices=[
            "prepare",
            "serve",
            "api-health",
            "db-check",
            "fixture",
            "cleanup",
            "sql-identity",
            "sql-business",
            "iam-sql",
        ],
    )
    parser.add_argument("--require-head", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare_state()
        elif args.action == "api-health":
            with urlopen(f"http://127.0.0.1:{profile()['api']}/_ops/ready", timeout=3) as response:
                if response.status != 200:
                    return 1
        else:
            load_database()
            if args.action == "serve":
                os.execv(
                    sys.executable,
                    [
                        sys.executable,
                        str(ROOT / "scripts/run_sso_api.py"),
                        "--identity-dir",
                        str(STATE),
                        "--host",
                        "0.0.0.0",
                        "--port",
                        str(profile()["api"]),
                    ],
                )
            elif args.action == "db-check":
                asyncio.run(check_database(args.require_head))
            else:
                return run_tool(args.action)
    except ContainerError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        print(
            f"Local container action failed ({type(error).__name__}); "
            "inspect configuration, no successful outcome assumed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
