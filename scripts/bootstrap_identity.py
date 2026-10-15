# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Link an existing Keycloak user and explicitly grant database permissions; dry-run by default.
import argparse
import asyncio
import json
import ssl
from pathlib import Path
from uuid import UUID

import httpx

from flowbridge.db import create_engine, create_session_factory
from flowbridge.identity.bootstrap import bootstrap_identity
from flowbridge.identity.directory import KeycloakDirectory
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError
from flowbridge.supplier_assistant.repository import SupplierRepository


async def run(args: argparse.Namespace) -> dict:
    overrides = (
        json.loads((args.identity_dir / "runtime.json").read_text("utf-8"))
        if args.identity_dir
        else {}
    )
    settings = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env", **overrides)
    if settings.database_url is None:
        raise RuntimeError("Database is not configured")
    verify = ssl.create_default_context(cafile=settings.oidc_ca_bundle)
    engine = create_engine(settings, use_null_pool=True)
    try:
        async with httpx.AsyncClient(
            verify=verify,
            timeout=settings.http_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as http:
            identity = await KeycloakDirectory(http, settings).resolve(str(args.subject))
            async with create_session_factory(engine)() as session:
                async with session.begin() as transaction:
                    result = await bootstrap_identity(
                        SupplierRepository(session),
                        identity,
                        roles=["ADMIN"] if args.bootstrap_admin else args.grant,
                        department_id=args.department,
                        granted_by=args.granted_by,
                        first_admin=args.bootstrap_admin,
                        display_name=args.display_name,
                    )
                    if not args.apply:
                        await transaction.rollback()
        return {"applied": args.apply, **result}
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Link Keycloak identity; rollback unless --apply")
    parser.add_argument("--subject", type=UUID, required=True, help="Existing Keycloak user UUID")
    parser.add_argument(
        "--identity-dir", type=Path, help="Optional generated local identity profile"
    )
    parser.add_argument("--display-name")
    parser.add_argument("--department", type=UUID)
    parser.add_argument(
        "--grant",
        action="append",
        default=[],
        choices=["EMPLOYEE", "ADMIN", "APPROVER", "WAREHOUSE", "AI_SERVICE", "PO_SERVICE"],
    )
    parser.add_argument("--granted-by", type=UUID, help="Existing active application ADMIN UUID")
    parser.add_argument("--bootstrap-admin", action="store_true", help="Only the first ADMIN")
    parser.add_argument(
        "--apply", action="store_true", help="Commit; otherwise validate and rollback"
    )
    args = parser.parse_args()
    if args.bootstrap_admin and (args.grant or args.granted_by):
        parser.error("--bootstrap-admin cannot be combined with --grant or --granted-by")
    try:
        result = asyncio.run(run(args))
    except CoreError as error:
        print(f"Bootstrap denied: {error.code}")
        return 1
    except Exception:
        print(
            "Bootstrap failed; no successful commit confirmed. "
            "Check configuration and retry safely."
        )
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
