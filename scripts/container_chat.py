# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Container entrypoints extend the canonical dev helper; no credentials in Compose environment.
import argparse
import asyncio
import json
import os
import secrets
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.request import Request, urlopen

CHAT_CONFIG = Path("/chat-config")
LANGFLOW_CONFIG = Path("/langflow-config")
LANGFLOW_STATE = Path("/var/lib/langflow")
MODEL_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL")


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n", encoding="utf-8", newline="\n")
    temporary.chmod(0o640)
    temporary.replace(path)


def seed_owner(path: Path) -> str:
    # The published backup is a stable single file mounted as a read-only Docker secret.
    # immutable prevents WAL-mode SQLite from trying to create journal sidecars there.
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        rows = connection.execute(
            "SELECT f.endpoint_name,u.username,u.is_active FROM flow f "
            'JOIN "user" u ON f.user_id=u.id WHERE f.endpoint_name IN '
            "('flowbridge-ver2-central','flowbridge-ver2-database',"
            "'flowbridge-ver2-prediction','flowbridge-ver2-knowledge')"
        ).fetchall()
    if (
        len(rows) != 4
        or len({r[0] for r in rows}) != 4
        or len({r[1] for r in rows}) != 1
        or not all(r[1] and r[2] for r in rows)
    ):
        raise ValueError("Seed must contain all four ver2 flows and one active native owner")
    return rows[0][1]


def prepare_chat(
    state: Path = LANGFLOW_STATE,
    private: Path = LANGFLOW_CONFIG,
    backend: Path = CHAT_CONFIG,
    seed: Path = Path("/run/secrets/langflow-seed"),
    seed_key: Path = Path("/run/secrets/langflow-key-source"),
    agent: Path = Path("/run/secrets/agent-source"),
    api_port: int = 18082,
    langflow_port: int = 7861,
) -> None:
    from dotenv import dotenv_values

    marker = private / "profile.json"
    expected = {"version": 1, "api_port": api_port, "langflow_port": langflow_port}
    if marker.exists():
        if json.loads(marker.read_text("utf-8")) != expected:
            raise ValueError("Chat ports changed; reuse the original ports or choose a new project")
        if not all((state / name).is_file() for name in ("langflow.db", "secret_key")):
            raise ValueError("Incomplete Langflow state; no reimport or secret rotation performed")
        runtime = json.loads((private / "runtime.json").read_text("utf-8"))
        chat = json.loads((backend / "runtime.json").read_text("utf-8"))
    else:
        if any(p.exists() and any(p.iterdir()) for p in (state, private, backend)):
            raise ValueError("Partial chat state exists; inspect it before retrying")
        owner = seed_owner(seed)
        if not seed_key.read_bytes().strip():
            raise ValueError("The original Langflow encryption key is required")
        state.mkdir(parents=True, exist_ok=True)
        key, bridge = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        runtime = {
            "LANGFLOW_SUPERUSER": owner,
            "LANGFLOW_SUPERUSER_PASSWORD": secrets.token_urlsafe(48),
            "LANGFLOW_API_KEY_SOURCE": "env",
            "LANGFLOW_API_KEY": key,
            "LANGFLOW_AUTO_LOGIN": "false",
            "LANGFLOW_SKIP_AUTH_AUTO_LOGIN": "false",
            "FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET": bridge,
            "FLOWBRIDGE_LANGFLOW_FLOW_ID": "flowbridge-ver2-central",
            "FLOWBRIDGE_API_ORIGIN": f"http://127.0.0.1:{api_port}",
            "FLOWBRIDGE_AGENT_ENV_FILE": "/tmp/flowbridge-no-local-caller.env",
        }
        chat = {
            "FLOWBRIDGE_CHAT_ENABLED": "true",
            "FLOWBRIDGE_LANGFLOW_URL": f"http://127.0.0.1:{langflow_port}",
            "FLOWBRIDGE_LANGFLOW_FLOW_ID": "flowbridge-ver2-central",
            "FLOWBRIDGE_LANGFLOW_API_KEY": key,
            "FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET": bridge,
        }
        shutil.copyfile(seed, state / "langflow.db")
        shutil.copyfile(seed_key, state / "secret_key")
    # Stable queue key stays backend-only; never copied to Langflow or rotated on restart.
    if "FLOWBRIDGE_AGENT_HUB_CREDENTIAL_KEY" not in chat:
        chat["FLOWBRIDGE_AGENT_HUB_CREDENTIAL_KEY"] = secrets.token_urlsafe(48)
    # Provider keys are refreshed deliberately during prepare; caller credentials are excluded.
    values = dotenv_values(agent, interpolate=False)
    for key in MODEL_KEYS:
        runtime.pop(key, None)
        if values.get(key):
            runtime[key] = values[key]
    write_json(private / "runtime.json", runtime)
    write_json(backend / "runtime.json", chat)
    write_json(marker, expected)
    if hasattr(os, "chown"):
        for directory in (state, private, backend):
            for path in [directory, *directory.rglob("*")]:
                os.chown(path, 10001, 0)
                path.chmod(0o770 if path.is_dir() else 0o640)
    print(
        "Chat configuration ready; existing flows/keys preserved; no model or business API called."
    )


def load_private(path: Path) -> None:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict) or any(not isinstance(v, str) for v in value.values()):
        raise ValueError("Invalid private runtime configuration")
    os.environ.update(value)


def backend_model_settings(source: Path = Path("/config/chat-models.json")) -> None:
    from flowbridge.settings import Settings

    values = json.loads(source.read_text("utf-8"))
    if any(
        not key.startswith(("FLOWBRIDGE_ML_", "FLOWBRIDGE_RAG_POSTGRES_"))
        or not isinstance(value, str)
        for key, value in values.items()
    ):
        raise ValueError("Invalid model settings")
    os.environ.update(values)
    for field, mount in (("ml_checkpoint_path", "/models"), ("ml_context_path", "/ml-context")):
        name = "FLOWBRIDGE_" + field.upper()
        filename = values.get(name, str(Settings.model_fields[field].default))
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        os.environ[name] = str(Path(mount) / filename)
    os.environ["FLOWBRIDGE_RAG_POSTGRES_MODEL_CACHE_PATH"] = "/models"
    os.environ["FLOWBRIDGE_ML_ENABLED"] = os.environ.get("FLOWBRIDGE_CHAT_ML", "false")
    os.environ["FLOWBRIDGE_RAG_POSTGRES_ENABLED"] = os.environ.get("FLOWBRIDGE_CHAT_RAG", "false")


def health() -> None:
    private = json.loads((LANGFLOW_CONFIG / "runtime.json").read_text("utf-8"))
    port = int(os.environ.get("FLOWBRIDGE_CHAT_PORT", "7861"))
    request = Request(
        f"http://127.0.0.1:{port}/_ops/flowbridge",
        headers={"X-FlowBridge-Bridge-Secret": private["FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET"]},
    )
    with urlopen(request, timeout=3) as response:
        result = json.load(response)
        if result != {"protocol": "flowbridge-caller-v1", "flow_id": "flowbridge-ver2-central"}:
            raise ValueError("Bridge is not ready")


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal chat Compose entrypoint")
    parser.add_argument(
        "action",
        choices=[
            "prepare",
            "serve",
            "langflow",
            "langflow-health",
            "db-check",
            "migrate",
            "bootstrap",
        ],
    )
    args, bootstrap_args = parser.parse_known_args()
    if bootstrap_args and args.action != "bootstrap":
        parser.error("Unexpected arguments")
    try:
        if args.action == "langflow-health":
            health()
        elif args.action == "langflow":
            load_private(LANGFLOW_CONFIG / "runtime.json")
            port = os.environ.get("FLOWBRIDGE_CHAT_PORT", "7861")
            os.execv(
                sys.executable,
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "flowbridge_agent.serving:create_app",
                    "--factory",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    port,
                    "--no-access-log",
                ],
            )
        else:
            from container_dev import (
                CONFIG,
                ROOT,
                STATE,
                check_database,
                load_database,
                prepare_state,
            )

            if args.action == "prepare":
                prepare_state()
                from dotenv import dotenv_values

                models = {
                    key: value
                    for key, value in dotenv_values(
                        "/run/secrets/database-source", interpolate=False
                    ).items()
                    if key.startswith(("FLOWBRIDGE_ML_", "FLOWBRIDGE_RAG_POSTGRES_"))
                    and value is not None
                }
                write_json(CONFIG / "chat-models.json", models)
                prepare_chat(
                    api_port=int(os.environ.get("FLOWBRIDGE_DEV_API_PORT", "18082")),
                    langflow_port=int(os.environ.get("FLOWBRIDGE_CHAT_PORT", "7861")),
                )
            else:
                load_database()
                # Database tooling does not need the chat adapter's secret/OIDC settings.
                os.environ["FLOWBRIDGE_CHAT_ENABLED"] = "false"
                if args.action == "db-check":
                    asyncio.run(check_database(require_head=True))
                elif args.action == "migrate":
                    os.execv(sys.executable, [sys.executable, "-m", "alembic", "upgrade", "head"])
                elif args.action == "bootstrap":
                    os.execv(
                        sys.executable,
                        [
                            sys.executable,
                            str(ROOT / "scripts/bootstrap_identity.py"),
                            "--identity-dir",
                            str(STATE),
                            *bootstrap_args,
                        ],
                    )
                else:
                    load_private(CHAT_CONFIG / "runtime.json")
                    backend_model_settings()
                    if os.environ["FLOWBRIDGE_RAG_POSTGRES_ENABLED"] == "true":
                        operator = json.loads((STATE / "operator.json").read_text("utf-8"))
                        os.environ["FLOWBRIDGE_LANGFLOW_AI_CLIENT_ID"] = "flowbridge-ai"
                        os.environ["FLOWBRIDGE_LANGFLOW_AI_CLIENT_SECRET"] = operator[
                            "service_secrets"
                        ]["flowbridge-ai"]
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
                            os.environ.get("FLOWBRIDGE_DEV_API_PORT", "18082"),
                        ],
                    )
    except Exception as exc:
        print(
            f"Chat container action failed ({type(exc).__name__}); check config/state privately.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
