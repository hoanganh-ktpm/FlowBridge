import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.core.data_sources.discovery import PostgresSchemaDiscovery, SourceSnapshot, fk_suggestions
from app.core.hasura_provisioner.adapters import EnvironmentSecretResolver
from app.core.hasura_provisioner.errors import HasuraError
from app.core.hasura_provisioner.settings import HasuraSettings

from .manifest import ManifestError, manifest_schema, parse_manifest
from .onboarding import MissingIntegration, discovery_source
from .planning import PlanContext, compile_manifest


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="flowbridge")
    command = root.add_subparsers(dest="command", required=True)
    onboard = command.add_parser("onboard")
    actions = onboard.add_subparsers(dest="action", required=True)
    for action in ("validate", "discover", "plan", "apply"):
        entry = actions.add_parser(action)
        entry.add_argument("--config", required=True, type=Path)
        entry.add_argument("--json", action="store_true")
        if action == "plan":
            entry.add_argument("--snapshot", type=Path)
    status = actions.add_parser("status")
    status.add_argument("--application", required=True)
    status.add_argument("--json", action="store_true")
    schema = actions.add_parser("schema")
    schema.add_argument("--json", action="store_true")
    return root


def read_file(path: Path) -> str:
    if path.stat().st_size > 2_000_000:
        raise ManifestError("Configuration file exceeds 2 MB")
    return path.read_text(encoding="utf-8-sig")


async def discover(manifest):
    settings = HasuraSettings()
    resolver = EnvironmentSecretResolver(settings.secret_bindings)
    adapter = PostgresSchemaDiscovery(resolver, settings.allowed_database_hosts)
    return [
        await adapter.discover(discovery_source(manifest, source)) for source in manifest.sources
    ]


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == "schema":
            output = manifest_schema()
        elif args.action in {"apply", "status"}:
            raise MissingIntegration(
                "Core persistence/authorization/job adapters are missing; command is disabled"
            )
        else:
            manifest = parse_manifest(read_file(args.config))
            if args.action == "validate":
                output = {
                    "valid": True,
                    "manifest_version": manifest.manifest_version,
                    "application": manifest.application.key,
                }
            elif args.action == "discover":
                snapshots = asyncio.run(discover(manifest))
                output = {
                    "snapshots": [
                        item.model_dump(mode="json", by_alias=True) for item in snapshots
                    ],
                    "suggestions": [
                        suggestion for item in snapshots for suggestion in fk_suggestions(item)
                    ],
                }
            else:
                snapshots = []
                if args.snapshot:
                    value = json.loads(read_file(args.snapshot))
                    snapshots = [SourceSnapshot.model_validate(item) for item in value["snapshots"]]
                    if len({item.source_key for item in snapshots}) != len(snapshots):
                        raise ManifestError("Duplicate snapshot source key")
                context = PlanContext(snapshots={item.source_key: item for item in snapshots})
                plan = compile_manifest(manifest, context)
                output = plan.model_dump(mode="json", by_alias=True)
                output["valid"] = plan.valid
                output["ready_for_dispatch"] = plan.ready_for_dispatch
                output["evidence"] = "OFFLINE_SNAPSHOT" if snapshots else "DISCOVERY_UNKNOWN"
                if not plan.valid:
                    print(json.dumps(output, ensure_ascii=False, indent=2))
                    return 2
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (ManifestError, MissingIntegration, HasuraError) as error:
        message = str(error)
    except (OSError, ValueError, KeyError, ValidationError):
        message = (
            "Invalid configuration, discovery input or missing integration; no changes applied"
        )
    except Exception:
        message = "Onboarding operation failed; diagnostics omitted to protect credentials"
    print(json.dumps({"error": message, "applied": False}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.exit(main())
