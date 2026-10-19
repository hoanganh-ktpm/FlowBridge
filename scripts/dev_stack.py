# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Standard-library orchestration of Compose; no custom container, IAM or migration engine.
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


class StackError(Exception):
    pass


class Stack:
    def __init__(
        self, project: str, env_file: Path, api_port: int, iam_port: int, *, test_mode: bool = False
    ) -> None:
        if not re.fullmatch(r"flowbridge-[a-z0-9][a-z0-9-]{0,45}", project):
            raise StackError(
                "Project must start with flowbridge- and contain lowercase letters/digits/hyphens"
            )
        if (
            not all(1024 <= port <= 65535 for port in (api_port, iam_port))
            or len({api_port, iam_port, 8761, 8762, 9000}) != 5
        ):
            raise StackError("Choose distinct API/IAM ports outside 8761/8762/9000")
        self.project = project
        self.env_file = env_file.resolve()
        self.api_port, self.iam_port = api_port, iam_port
        self.test_mode = test_mode
        self.env = {
            **os.environ,
            "FLOWBRIDGE_DEV_ENV_FILE": str(self.env_file),
            "FLOWBRIDGE_DEV_API_PORT": str(api_port),
            "FLOWBRIDGE_DEV_IAM_PORT": str(iam_port),
            "FLOWBRIDGE_DEV_TEST_MODE": str(test_mode).lower(),
        }

    def command(self, *args: str) -> list[str]:
        # Include optional clients/tools when stopping this project, preserving volumes by default.
        profiles = ["--profile", "*"] if args and args[0] == "down" else []
        return [
            "docker",
            "compose",
            "--project-directory",
            str(ROOT),
            "--project-name",
            self.project,
            "--env-file",
            str(ROOT / "infra/dev/images.env"),
            "-f",
            str(ROOT / "infra/dev/compose.yaml"),
            *profiles,
            *args,
        ]

    def run(
        self, *args: str, check: bool = True, capture: bool = False
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            self.command(*args),
            cwd=ROOT,
            env=self.env,
            check=False,
            capture_output=capture,
            text=True,
        )
        if check and result.returncode:
            raise StackError(
                "Compose action failed: " + args[0] + "; inspect the preceding diagnostic"
            )
        return result

    def check_engine(self, *, daemon: bool = True) -> None:
        if shutil.which("docker") is None:
            raise StackError(
                "Docker CLI is not installed. Install a Linux-container runtime first; "
                "source preparation does not install it."
            )
        result = self.run("version", "--short", capture=True)
        version = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
        if version is None or tuple(map(int, version.groups())) < (2, 35, 0):
            raise StackError(
                "Docker Compose >= 2.35.0 is required (volume subpaths and lifecycle flags)"
            )
        if daemon:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.OSType}}"],
                cwd=ROOT,
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode or result.stdout.strip() != "linux":
                raise StackError("Start a Docker Engine using Linux containers")

    def save_run(self, phase: str) -> None:
        directory = ROOT / ".cache/dev-stack"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{self.project}.json").write_text(
            json.dumps(
                {
                    "project": self.project,
                    "env_file": str(self.env_file),
                    "api_port": self.api_port,
                    "iam_port": self.iam_port,
                    "test_mode": self.test_mode,
                    "phase": phase,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def has_volumes(self) -> bool:
        result = subprocess.run(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{.Name}}",
            ],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise StackError("Cannot verify ownership of test volumes; no cleanup authorized")
        return bool(result.stdout.strip())

    def test_sso(self) -> None:
        # Only a freshly generated project may have its volumes removed automatically.
        if not re.fullmatch(r"flowbridge-test-[a-f0-9]{12}", self.project):
            raise StackError("SSO acceptance requires an isolated generated test project")
        if self.run("ps", "-a", "-q", capture=True).stdout.strip():
            raise StackError("Test project already has containers; recover it before retrying")
        if self.has_volumes():
            raise StackError("Test project already has volumes; do not reuse it for acceptance")
        for port in (self.api_port, self.iam_port, 8761, 8762):
            with socket.socket() as listener:
                try:
                    listener.bind(("127.0.0.1", port))
                except OSError:
                    raise StackError(
                        f"Port {port} is occupied; stop the dev stack or choose other API/IAM ports"
                    ) from None
        self.save_run("starting")
        primary_error: BaseException | None = None
        cleanup_ok = True
        fixture_attempted = False
        try:
            self.run("build", "prepare", "check-db")
            self.run("run", "--rm", "--no-deps", "prepare")
            # Read-only revision check before IAM startup or any fixture write.
            self.run(
                "run",
                "--rm",
                "--no-deps",
                "check-db",
                "python",
                "scripts/container_dev.py",
                "db-check",
                "--require-head",
            )
            self.run("build", "api", "tools", "browser")
            self.run("up", "-d", "--wait", "--wait-timeout", "240", "api")
            fixture_attempted = True
            self.save_run("fixture-attempted")
            self.run(
                "run", "--rm", "--no-deps", "tools", "python", "scripts/container_dev.py", "fixture"
            )
            self.run(
                "run", "--rm", "--no-deps", "tools", "python", "scripts/container_dev.py", "iam-sql"
            )
            self.run("run", "--rm", "--no-deps", "browser")
        except BaseException as error:
            primary_error = error
        finally:
            if fixture_attempted:
                try:
                    cleanup_ok = (
                        self.run(
                            "run",
                            "--rm",
                            "--no-deps",
                            "tools",
                            "python",
                            "scripts/container_dev.py",
                            "cleanup",
                            check=False,
                        ).returncode
                        == 0
                    )
                except BaseException:
                    cleanup_ok = False
            # Preserve fixture manifest, credentials and IAM DB if cleanup is uncertain.
            down = ("down", "--volumes") if cleanup_ok else ("down",)
            try:
                stopped = self.run(*down, check=False).returncode == 0
            except BaseException:
                stopped = False
            self.save_run(
                ("passed" if primary_error is None else "failed-cleaned")
                if cleanup_ok and stopped
                else "recovery-required"
            )
            if not cleanup_ok or not stopped:
                raise StackError(
                    f"Cleanup/stop needs attention for {self.project}; "
                    "preserve volumes and follow docs/DEV-CONTAINERS.md"
                ) from primary_error
        if primary_error is not None:
            raise primary_error


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Compose development/test lifecycle")
    parser.add_argument(
        "action",
        choices=[
            "doctor",
            "config",
            "up",
            "down",
            "logs",
            "status",
            "unit",
            "test-sso",
            "sql-identity",
            "sql-business",
            "cleanup-fixture",
        ],
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--project")
    parser.add_argument("--api-port", type=int, default=18082)
    parser.add_argument("--iam-port", type=int, default=19444)
    parser.add_argument(
        "--clients", action="store_true", help="Start public development clients A/B"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show configuration without invoking Docker"
    )
    parser.add_argument(
        "--recover", type=Path, help="Use saved .cache/dev-stack/<project>.json parameters"
    )
    args = parser.parse_args()
    try:
        if args.recover:
            if args.action not in {"up", "down", "logs", "status", "cleanup-fixture"}:
                raise StackError("Recovery parameters are only for lifecycle/fixture recovery")
            saved = json.loads(args.recover.read_text("utf-8"))
            stack = Stack(
                saved["project"],
                Path(saved["env_file"]),
                saved["api_port"],
                saved["iam_port"],
                test_mode=saved["test_mode"],
            )
        else:
            if args.action == "test-sso" and args.project:
                raise StackError(
                    "test-sso chooses a fresh project; do not reuse an existing project"
                )
            project = args.project or (
                "flowbridge-test-" + uuid4().hex[:12]
                if args.action == "test-sso"
                else "flowbridge-dev"
            )
            stack = Stack(
                project,
                args.env_file
                or (ROOT / ".env" if (ROOT / ".env").exists() else ROOT / ".env.example"),
                args.api_port,
                args.iam_port,
                test_mode=args.action == "test-sso",
            )
        if not stack.env_file.is_file():
            raise StackError("Environment file does not exist; provide --env-file")
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "action": args.action,
                        "project": stack.project,
                        "compose_command": stack.command("config", "--quiet"),
                        "api": f"http://127.0.0.1:{stack.api_port}",
                        "issuer": f"https://localhost:{stack.iam_port}/realms/flowbridge",
                        "db_env_file": str(stack.env_file),
                        "test_mode": stack.test_mode,
                        "automatic_migration_or_seed": False,
                    },
                    indent=2,
                )
            )
            return 0
        stack.check_engine(daemon=args.action != "config")
        if args.action in {"doctor", "config"}:
            stack.run("config", "--quiet")
            print(
                "PASS: Compose configuration"
                + (" and Linux engine" if args.action == "doctor" else "")
            )
        elif args.action == "unit":
            stack.run("run", "--build", "--rm", "--no-deps", "unit")
        elif args.action == "test-sso":
            stack.test_sso()
        elif args.action == "up":
            stack.run("run", "--build", "--rm", "--no-deps", "prepare")
            stack.run("run", "--build", "--rm", "--no-deps", "check-db")
            extra = ["client-a", "client-b"] if args.clients else []
            stack.run("up", "-d", "--build", "--wait", "--wait-timeout", "240", "api", *extra)
            print(f"API: http://127.0.0.1:{stack.api_port}/docs; local IAM state is persistent.")
        elif args.action in {"sql-identity", "sql-business"}:
            stack.run("run", "--build", "--rm", "--no-deps", "prepare")
            if args.action == "sql-business":
                stack.run(
                    "run",
                    "--build",
                    "--rm",
                    "--no-deps",
                    "check-db",
                    "python",
                    "scripts/container_dev.py",
                    "db-check",
                    "--require-head",
                )
            # SQL-only tools use the standalone check-db service: IAM/API are unnecessary.
            stack.run(
                "run",
                "--build",
                "--rm",
                "--no-deps",
                "check-db",
                "python",
                "scripts/container_dev.py",
                args.action,
            )
        elif args.action == "cleanup-fixture":
            stack.run(
                "run", "--rm", "--no-deps", "tools", "python", "scripts/container_dev.py", "cleanup"
            )
        elif args.action == "logs":
            stack.run("logs", "--tail", "100", "api", "identity")
        elif args.action == "status":
            stack.run("ps", "-a")
        else:
            stack.run("down")
    except KeyboardInterrupt:
        print("Interrupted; inspect saved run state before retrying.", file=sys.stderr)
        return 130
    except (StackError, OSError, ValueError, KeyError) as error:
        print(
            str(error)
            if isinstance(error, StackError)
            else "Invalid local configuration or unavailable tool; no successful run assumed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
