# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Reuse the standard Compose/fixture lifecycle in a separate gateway test project.
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from dev_stack import ROOT, Stack, StackError


class GatewayStack(Stack):
    def command(self, *args: str) -> list[str]:
        command = super().command(*args)
        command[command.index(str(ROOT / "compose.yaml"))] = str(
            ROOT / "infra/gateway/compose.yaml"
        )
        index = command.index("-f")
        command[index:index] = ["--env-file", str(ROOT / "infra/gateway/images.env")]
        return command

    def receiver_address(self) -> str:
        container = self.run("ps", "-q", "identity", capture=True).stdout.strip()
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        networks = json.loads(result.stdout)
        return networks[self.project + "_backend"]["IPAddress"]

    def verify_logs(self) -> None:
        # Captured output is inspected in memory, never printed or written to evidence.
        # APISIX's upstream Nginx template buffers access logs with flush=3 seconds.
        time.sleep(4)
        logs = self.run("logs", "--no-color", "gateway", capture=True).stdout
        receiver_logs = self.run("logs", "--no-color", "api", capture=True).stdout
        values = self.run(
            "run",
            "--rm",
            "--no-deps",
            "tools",
            "python",
            "-c",
            "import json; from pathlib import Path; "
            "s=Path('/state'); "
            "v=json.loads((s/'gateway-sensitive.json').read_text())['tokens']; "
            "r=json.loads((s/'runtime.json').read_text()); "
            "o=json.loads((s/'operator.json').read_text()); "
            "v += [r['oidc_client_secret'], o['bootstrap_password']]; "
            "v += list(o['service_secrets'].values()); "
            "v += [u['password'] for u in o['test_users'].values()]; "
            "print(json.dumps(v))",
            capture=True,
        ).stdout
        if any("gateway-human-01" not in output for output in (logs, receiver_logs)):
            raise StackError("Gateway/receiver correlation assertion failed")
        if "203.0.113.10" in logs:
            raise StackError("Gateway log correlation/proxy boundary assertion failed")
        blocked_paths = (
            "/internal/v1/core/data-schemas",
            "/admin/v1/users",
            "/apisix/admin/routes",
            "/docs",
            "/openapi.json",
            "/api/v1/me",
            "/api/v1/core/identity/unknown",
        )
        if any(path in receiver_logs for path in blocked_paths):
            raise StackError("A public-denied route reached the receiver")
        if any(value and value in logs + receiver_logs for value in json.loads(values)):
            raise StackError("Gateway logs contain sensitive fixture material; inspect privately")
        print("PASS: gateway/receiver correlation, denied-route absence and secret redaction")

    def acceptance(self) -> None:
        if not re.fullmatch(r"flowbridge-gateway-test-[a-f0-9]{12}", self.project):
            raise StackError("Acceptance requires a new isolated gateway test project")
        if self.run("ps", "-a", "-q", capture=True).stdout.strip() or self.has_volumes():
            raise StackError("Project already has state; inspect/recover before retrying")
        self.save_run("gateway-starting")
        fixture_attempted = False
        cleanup_ok = True
        passed = False
        try:
            self.run(
                "build",
                "prepare",
                "api",
                "check-db",
                "tools",
                "browser",
                "external",
                "slow-upstream",
            )
            self.run("run", "--rm", "--no-deps", "prepare")
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
            self.run(
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "240",
                "api",
                "gateway",
                "gateway-timeout",
            )
            fixture_attempted = True
            self.run("run", "--rm", "--no-deps", "tools")
            self.run(
                "run",
                "--rm",
                "--no-deps",
                "-e",
                "FLOWBRIDGE_GATEWAY_RECEIVER_IP=" + self.receiver_address(),
                "-e",
                "FLOWBRIDGE_GATEWAY_RECEIVER_PORT=" + str(self.api_port),
                "external",
            )
            self.run("run", "--rm", "--no-deps", "browser")
            self.verify_logs()
            passed = True
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
            if cleanup_ok:
                self.run("down", "--volumes")
                self.save_run("gateway-passed-cleaned" if passed else "gateway-failed-cleaned")
            else:
                self.run("down")
                self.save_run("gateway-recovery-required")
                raise StackError(
                    "Fixture cleanup failed; preserve volumes and recover before retry"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated APISIX/Keycloak/API acceptance")
    parser.add_argument("action", choices=["test", "config", "recover"])
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--project", help="Required for recovery; use the saved project name")
    parser.add_argument("--api-port", type=int, default=18092)
    parser.add_argument("--iam-port", type=int, default=19454)
    args = parser.parse_args()
    if args.action == "recover" and not args.project:
        parser.error("recover requires --project from .cache/dev-stack/<project>.json")
    project = args.project or "flowbridge-gateway-test-" + uuid4().hex[:12]
    if not re.fullmatch(r"flowbridge-gateway-test-[a-f0-9]{12}", project):
        parser.error("project must be flowbridge-gateway-test- followed by 12 hex digits")
    stack = GatewayStack(project, args.env_file, args.api_port, args.iam_port, test_mode=True)
    try:
        stack.check_engine(daemon=args.action != "config")
        if args.action == "test":
            stack.acceptance()
        elif args.action == "config":
            # Quiet validation: rendered config can contain environment values.
            stack.run("config", "--quiet")
        else:
            stack.run("up", "-d", "--wait", "identity")
            stack.run(
                "run", "--rm", "--no-deps", "tools", "python", "scripts/container_dev.py", "cleanup"
            )
            stack.run("down", "--volumes")
            stack.save_run("gateway-recovered")
    except StackError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
