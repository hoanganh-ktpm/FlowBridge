# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Compose orchestration only; source SQLite is backed up through SQLite's supported API.
import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from dev_stack import ROOT, Stack, StackError


def snapshot_langflow(source: Path, destination: Path) -> None:
    complete = destination / "complete.json"
    if complete.exists():
        if json.loads(complete.read_text("utf-8"))["source"] != str(source.resolve()):
            raise StackError("Snapshot belongs to another source; choose a new project")
        if not all((destination / name).is_file() for name in ("langflow.db", "secret_key")):
            raise StackError("Incomplete saved Langflow snapshot; inspect it before retrying")
        return
    if destination.exists() and any(destination.iterdir()):
        raise StackError("Partial snapshot exists; refusing to overwrite it")
    if not all((source / name).is_file() for name in ("langflow.db", "secret_key")):
        raise StackError(
            "Install/import ver2 in local Langflow first; DB and encryption key are required"
        )
    destination.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(
        (source / "langflow.db").resolve().as_uri() + "?mode=ro", uri=True
    ) as read:
        with sqlite3.connect(destination / "langflow.db") as write:
            read.backup(write)
            if write.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StackError("SQLite backup integrity check failed")
    shutil.copyfile(source / "secret_key", destination / "secret_key")
    complete.write_text(json.dumps({"version": 1, "source": str(source.resolve())}) + "\n", "utf-8")


class ChatStack(Stack):
    def __init__(
        self,
        project: str,
        env_file: Path,
        api_port: int = 18082,
        iam_port: int = 19444,
        langflow_port: int = 7861,
        *,
        ml: bool = False,
        rag: bool = False,
        langflow_data: Path = ROOT / "langflow/data",
        agent_env: Path = ROOT / "langflow/data/agent.env",
        models_dir: Path = ROOT / ".cache/models",
        ml_dir: Path = ROOT / ".cache/ml",
    ):
        super().__init__(project, env_file, api_port, iam_port)
        if not 1024 <= langflow_port <= 65535 or langflow_port in {
            api_port,
            iam_port,
            8761,
            8762,
            9000,
        }:
            raise StackError("Choose a distinct unprivileged Langflow port")
        self.directory = ROOT / ".cache/chat-stack" / project
        self.langflow_data = langflow_data.resolve()
        self.langflow_port = langflow_port
        self.env.update(
            {
                "FLOWBRIDGE_CHAT_PORT": str(langflow_port),
                "FLOWBRIDGE_CHAT_ML": str(ml).lower(),
                "FLOWBRIDGE_CHAT_RAG": str(rag).lower(),
                "FLOWBRIDGE_CHAT_SEED_DB": str(self.directory / "seed/langflow.db"),
                "FLOWBRIDGE_CHAT_SEED_KEY": str(self.directory / "seed/secret_key"),
                "FLOWBRIDGE_CHAT_AGENT_ENV": str(agent_env.resolve()),
                "FLOWBRIDGE_CHAT_MODELS_DIR": str(models_dir.resolve()),
                "FLOWBRIDGE_CHAT_ML_DIR": str(ml_dir.resolve()),
            }
        )

    def command(self, *args: str) -> list[str]:
        command = super().command(*args)
        index = command.index("-f")
        command[index:index] = ["--env-file", str(ROOT / "infra/chat/images.env")]
        index = command.index(str(ROOT / "infra/dev/compose.yaml")) + 1
        command[index:index] = ["-f", str(ROOT / "infra/chat/compose.yaml")]
        return command

    def check_engine(self, *, daemon: bool = True) -> None:
        super().check_engine(daemon=False)
        if not daemon:
            return
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.OSType}}"],
                env=self.env,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise StackError(
                "Docker Desktop is still starting; open it and wait for Linux Engine"
            ) from None
        if result.returncode or result.stdout.strip() != "linux":
            raise StackError("Open Docker Desktop and start its Linux Engine before retrying")

    def prepare(self) -> None:
        snapshot_langflow(self.langflow_data, self.directory / "seed")
        if not Path(self.env["FLOWBRIDGE_CHAT_AGENT_ENV"]).is_file():
            raise StackError("Configure the existing Langflow agent.env before starting chat")
        self.run("run", "--build", "--rm", "--no-deps", "prepare")

    def credentials(self) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "credentials.json"
        # Copy a file through Docker's supported API; secrets never pass container stdout/logs.
        self.run("cp", "identity:/state/operator.json", str(path), capture=True)
        operator = json.loads(path.read_text("utf-8"))
        value = {
            "keycloak_admin": {
                "username": operator["bootstrap_admin"],
                "password": operator["bootstrap_password"],
            },
            "keycloak_users": operator["test_users"],
            "note": "Keycloak accounts only. Langflow editor uses its own credentials.",
        }
        path.write_text(json.dumps(value, indent=2) + "\n", "utf-8")
        return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Keycloak/API/Langflow/client Compose lifecycle")
    parser.add_argument(
        "action",
        nargs="?",
        default="up",
        choices=[
            "doctor",
            "config",
            "up",
            "down",
            "status",
            "logs",
            "migrate",
            "credentials",
            "team-accounts",
        ],
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--project", default="flowbridge-chat")
    parser.add_argument("--api-port", type=int, default=18082)
    parser.add_argument("--iam-port", type=int, default=19444)
    parser.add_argument("--langflow-port", type=int, default=7861)
    parser.add_argument("--langflow-data", type=Path, default=ROOT / "langflow/data")
    parser.add_argument("--agent-env", type=Path, default=ROOT / "langflow/data/agent.env")
    parser.add_argument("--models-dir", type=Path, default=ROOT / ".cache/models")
    parser.add_argument("--ml-dir", type=Path, default=ROOT / ".cache/ml")
    parser.add_argument("--ml", action="store_true", help="Build existing optional ML dependencies")
    parser.add_argument(
        "--rag", action="store_true", help="Build existing optional PostgreSQL RAG dependencies"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--granted-by", help="Application ADMIN UUID for team-accounts setup")
    args = parser.parse_args()
    if args.granted_by and args.action != "team-accounts":
        parser.error("--granted-by is only supported for team-accounts")
    try:
        stack = ChatStack(
            args.project,
            args.env_file,
            args.api_port,
            args.iam_port,
            args.langflow_port,
            ml=args.ml,
            rag=args.rag,
            langflow_data=args.langflow_data,
            agent_env=args.agent_env,
            models_dir=args.models_dir,
            ml_dir=args.ml_dir,
        )
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "action": args.action,
                        "project": stack.project,
                        "api_port": stack.api_port,
                        "iam_port": stack.iam_port,
                        "langflow_port": stack.langflow_port,
                        "ml": args.ml,
                        "rag": args.rag,
                        "env_file": str(stack.env_file),
                    }
                )
            )
            return 0
        stack.check_engine(daemon=args.action != "config")
        if args.action in {"doctor", "config"}:
            stack.run("--profile", "*", "config", "--quiet")
            print(
                "PASS: chat Compose configuration"
                + (" and Linux engine" if args.action == "doctor" else "")
            )
        elif args.action in {"up", "migrate"}:
            stack.prepare()
            if args.action == "migrate":
                stack.run("run", "--build", "--rm", "--no-deps", "migrate")
            else:
                stack.run("run", "--build", "--rm", "--no-deps", "check-db")
                stack.run(
                    "up",
                    "-d",
                    "--build",
                    "--wait",
                    "--wait-timeout",
                    "420",
                    "api",
                    "langflow",
                    "client-a",
                )
                stack.run(
                    "exec",
                    "-T",
                    "langflow",
                    "python",
                    "/opt/flowbridge/scripts/upgrade_chat_flows.py",
                )
                print(f"Client: http://127.0.0.1:8761; API: http://127.0.0.1:{stack.api_port}/docs")
                print(f"Langflow: http://127.0.0.1:{stack.langflow_port}; volumes are persistent.")
        elif args.action == "team-accounts":
            bootstrap = ["--granted-by", args.granted_by] if args.granted_by else []
            stack.run(
                "run",
                "--build",
                "--rm",
                "--no-deps",
                "tools",
                "python",
                "scripts/container_chat.py",
                "team-accounts",
                *bootstrap,
            )
        elif args.action == "credentials":
            print("Credentials saved privately to:", stack.credentials())
        elif args.action == "logs":
            stack.run("logs", "--tail", "80", "api", "langflow", "identity")
        elif args.action == "status":
            stack.run("ps", "-a")
        else:
            stack.run("down")
    except (StackError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, StackError) else type(exc).__name__
        print("Chat stack failed: " + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
