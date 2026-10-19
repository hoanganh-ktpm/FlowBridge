# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.

# One-shot Compose jobs call the existing preparation, Alembic and account tooling.
import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from chat_stack import snapshot_langflow
from container_chat import LANGFLOW_CONFIG, prepare_chat, write_json
from container_dev import CONFIG, ROOT, STATE, check_database, load_database, prepare_state


def enabled(name: str, default: str = "true") -> bool:
    value = os.environ.get(name, default).lower()
    if value not in {"true", "false"}:
        raise ValueError(name + " must be true or false")
    return value == "true"


def prepare(source: Path = Path("/seed-source"), temporary: Path = Path("/tmp/chat-seed")) -> None:
    from dotenv import dotenv_values

    # A new volume needs a consistent SQLite backup; an existing volume retains its own flows/key.
    seed = temporary / "langflow.db"
    key = temporary / "secret_key"
    if not (LANGFLOW_CONFIG / "profile.json").exists():
        snapshot_langflow(source, temporary)
    prepare_state()
    models = {
        key: value
        for key, value in dotenv_values("/run/secrets/database-source", interpolate=False).items()
        if key.startswith(("FLOWBRIDGE_ML_", "FLOWBRIDGE_RAG_POSTGRES_")) and value is not None
    }
    write_json(CONFIG / "chat-models.json", models)
    prepare_chat(
        seed=seed,
        seed_key=key,
        api_port=int(os.environ.get("FLOWBRIDGE_DEV_API_PORT", "18082")),
        langflow_port=int(os.environ.get("FLOWBRIDGE_CHAT_PORT", "7861")),
    )


async def initialize_database() -> None:
    import sqlalchemy as sa

    from flowbridge.db import create_engine
    from flowbridge.settings import Settings

    load_database()
    if enabled("FLOWBRIDGE_COMPOSE_MIGRATE"):
        settings = Settings(_env_file=None)
        if settings.database_url is None:
            raise ValueError("Configure the application database before docker compose up")
        engine = create_engine(settings, use_null_pool=True)
        try:
            # Coordinate simultaneous team startups against the same PostgreSQL database.
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text("SELECT pg_advisory_xact_lock(hashtext('flowbridge.compose.migrate'))")
                )
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", "head"],
                    cwd=ROOT,
                    check=True,
                    timeout=300,
                )
        finally:
            await engine.dispose()
    await check_database(require_head=True)


async def setup_accounts() -> None:
    if not enabled("FLOWBRIDGE_TEAM_ACCOUNTS_ENABLED"):
        print("Team account setup disabled in configuration")
        return
    from team_accounts import setup

    load_database()
    actor = os.environ.get("FLOWBRIDGE_TEAM_GRANTED_BY", "").strip()
    result = await setup(
        argparse.Namespace(identity_dir=STATE, granted_by=UUID(actor) if actor else None)
    )
    print(json.dumps(result))
    print("Team accounts ready")


def main() -> int:
    parser = argparse.ArgumentParser(description="Default Compose startup phases")
    parser.add_argument("action", choices=["prepare", "database", "accounts"])
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare()
        elif args.action == "database":
            asyncio.run(initialize_database())
        else:
            asyncio.run(setup_accounts())
    except Exception as exc:
        # CLI/library errors can carry DSNs or IdP responses; log only their class and safe advice.
        print(
            f"Compose phase {args.action} failed ({type(exc).__name__}). "
            "Check compose.env, database/provider files and service state; "
            "existing volumes retained.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
