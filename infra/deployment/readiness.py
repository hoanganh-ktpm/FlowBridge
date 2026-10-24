"""Probe published application endpoints without relying on image shell tools."""

import argparse
import base64
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import yaml


def check(stack: Path) -> list[str]:
    compose = yaml.safe_load((stack / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    failures = []
    for service, path in (
        ("hasura", "/healthz"),
        ("flowable", "/flowable-rest/actuator/health"),
    ):
        if service not in services:
            continue
        port = services[service]["ports"][0].split(":")[1]
        headers = {}
        if service == "flowable":
            values = dict(
                line.split("=", 1)
                for line in (stack / "flowable.env").read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            credentials = (
                values["FLOWABLE_REST_APP_ADMIN_USER_ID"]
                + ":"
                + values["FLOWABLE_REST_APP_ADMIN_PASSWORD"]
            )
            headers["Authorization"] = "Basic " + base64.b64encode(
                credentials.encode("utf-8")
            ).decode("ascii")
        request = Request(f"http://127.0.0.1:{port}{path}", headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                body = response.read()
                if response.status != 200 or (
                    service == "flowable" and json.loads(body).get("status") != "UP"
                ):
                    failures.append(service)
        except (OSError, ValueError, KeyError, TypeError):
            failures.append(service)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stack", type=Path)
    args = parser.parse_args()
    try:
        failures = check(args.stack)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        print("readiness configuration invalid", file=sys.stderr)
        return 1
    if failures:
        print("not ready: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("application endpoints ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
