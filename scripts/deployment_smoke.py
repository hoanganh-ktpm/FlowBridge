"""Read-only smoke check for an installed deployment."""

import argparse
import json
import sys
from pathlib import Path

import yaml
from infra.deployment.readiness import check as check_readiness

from scripts.deployment_cli import SetupError, _compose, _run
from scripts.deployment_config import _env_file, validate
from scripts.deployment_render import IMAGES


def smoke(config_path, env_path):
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    errors = validate(config, _env_file(env_path), config_path.parent)
    if errors:
        raise SetupError("; ".join(errors))
    stack = config_path.parent / "infra" / "deployment" / "generated"
    compose = yaml.safe_load((stack / "compose.yaml").read_text(encoding="utf-8"))
    for service, image in IMAGES.items():
        if service in compose["services"] and compose["services"][service]["image"] != image:
            raise SetupError(f"{service} image differs from pinned deployment version")
    _compose(stack, config["name"], "config", "--quiet")
    for service in ("postgres", "keycloak"):
        if not config["services"][service]["enabled"]:
            continue
        container = _compose(stack, config["name"], "ps", "-q", service)
        if not container:
            raise SetupError(f"{service} is not running")
        state = _run(["docker", "inspect", "--format", "{{json .State.Health}}", container])
        if (json.loads(state) or {}).get("Status") != "healthy":
            raise SetupError(f"{service} is not healthy")
    failures = check_readiness(stack)
    if failures:
        raise SetupError("Application readiness failed: " + ", ".join(failures))
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("deployment.yaml"))
    parser.add_argument("--env", type=Path)
    args = parser.parse_args(argv)
    path = args.config.resolve()
    env = args.env or path.parent / "secrets" / "deployment.env"
    try:
        smoke(path, env)
    except (SetupError, OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(
            f"FAIL: {exc if isinstance(exc, SetupError) else type(exc).__name__}", file=sys.stderr
        )
        return 1
    print("PASS: pinned images, Compose, container health and application readiness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
