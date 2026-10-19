# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

import httpx
import sqlalchemy as sa
from fastapi import FastAPI, Request

from flowbridge.agent_hub.activities import validate_activity
from flowbridge.agent_hub.adapter import HubAdapter
from flowbridge.agent_hub.contract import HubError
from flowbridge.agent_hub.projection import ACTIVE, empty_result, failed_result, from_langflow
from flowbridge.agent_hub.repository import HubRepository
from flowbridge.agent_hub.service import HubService, merge_resources
from flowbridge.identity.authentication import verify_access_token
from flowbridge.models import agent_hub as db
from flowbridge.shared.errors import CoreError

logger = logging.getLogger(__name__)


class HubWorker:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        resources, settings = app.state.resources, app.state.settings
        self.service = HubService(
            resources.sessions, resources.http, settings, app.state.rag_postgres_service
        )
        self.adapter = HubAdapter(resources.http, settings)

    async def verify_schema(self) -> None:
        async with self.service.sessions() as session:
            for target in (db.runs, db.conversations, db.messages, db.events, db.cursors):
                await session.execute(sa.select(target).limit(0))
            # Only pgcrypto availability is checked; no user data, model or graph execution.
            available = await session.scalar(
                sa.text(
                    "SELECT to_regprocedure('extensions.pgp_sym_encrypt(text,text,text)') "
                    "IS NOT NULL"
                )
            )
            if not available:
                raise RuntimeError("Agent Hub requires pgcrypto in extensions and migration 0006")

    async def recover(self) -> None:
        async with self.service.sessions() as session, session.begin():
            repo = HubRepository(session)
            query = (
                sa.select(db.runs)
                .where(
                    sa.or_(
                        sa.and_(
                            db.runs.c.status.in_(["running", "cancelling"]),
                            db.runs.c.deadline < datetime.now(UTC),
                        ),
                        sa.and_(
                            db.runs.c.status == "queued",
                            db.runs.c.accepted_at
                            < datetime.now(UTC)
                            - timedelta(
                                seconds=self.service.settings.agent_hub_queue_timeout_seconds
                            ),
                        ),
                    ),
                )
                .with_for_update(skip_locked=True)
                .limit(20)
            )
            for row in (await session.execute(query)).mappings():
                run = dict(row)
                await self.service.finish(
                    repo,
                    run,
                    failed_result(
                        run["id"],
                        "QUEUE_TIMEOUT" if run["status"] == "queued" else "EXECUTION_INTERRUPTED",
                        "Không xác nhận được kết quả lượt chạy. Hệ thống không tự chạy lại.",
                    ),
                )
            await session.execute(
                sa.delete(db.cursors).where(db.cursors.c.expires_at < datetime.now(UTC))
            )

    async def claim(self) -> dict | None:
        async with self.service.sessions() as session, session.begin():
            query = (
                sa.select(db.runs)
                .where(db.runs.c.status == "queued")
                .order_by(db.runs.c.accepted_at, db.runs.c.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            row = (await session.execute(query)).mappings().first()
            if row is None:
                return None
            repo, run = HubRepository(session), dict(row)
            now = datetime.now(UTC)
            metadata = {**run["metadata"], "status": "running", "started_at": now.isoformat()}
            run = await repo.update(
                db.runs,
                run["id"],
                {
                    "status": "running",
                    "metadata": metadata,
                    "result": empty_result(run["id"], "running"),
                    "deadline": now
                    + timedelta(seconds=self.service.settings.langflow_timeout_seconds + 30),
                },
            )
            await repo.event(run, "run.started")
            return run

    async def activity(self, rid: str, event: dict) -> None:
        if event["type"] not in {
            "agent.started",
            "agent.completed",
            "tool.started",
            "tool.completed",
        }:
            raise ValueError("INVALID_ACTIVITY_TYPE")
        async with self.service.sessions() as session, session.begin():
            repo = HubRepository(session)
            run = await repo.one(db.runs, db.runs.c.id == rid, lock=True)
            if run["status"] not in ACTIVE:
                return
            previous = await repo.rows(
                db.events, db.events.c.run_id == rid, order=db.events.c.sequence
            )
            if len(previous) >= 500:
                raise ValueError("ACTIVITY_LIMIT_EXCEEDED")
            validate_activity(
                event, [row["body"] for row in previous], run["metadata"]["selected_agents"]
            )
            if "error" in event["data"]:
                event["data"]["error"]["trace_id"] = rid
            await repo.event(run, event["type"], event["data"], event["occurred_at"])
            agent = event["data"]["agent"]
            if event["type"] == "agent.started" and agent != "central":
                metadata = run["metadata"]
                selected = set(metadata["selected_agents"])
                if event["data"]["invoked_by"] == "central" and agent not in selected:
                    raise ValueError("UNSELECTED_PRIMARY_AGENT")
                if event["data"]["role"] == "dependency" and not (
                    agent == "database"
                    and event["data"]["invoked_by"] in {"prediction", "knowledge"}
                ):
                    raise ValueError("INVALID_DEPENDENCY_AGENT")
                metadata["used_agents"] = sorted(set(metadata["used_agents"]) | {agent})
                metadata["dependency_agents"] = sorted(set(metadata["used_agents"]) - selected)
                await repo.update(db.runs, rid, {"metadata": metadata})

    async def execute(self, run: dict) -> tuple[dict, dict]:
        async with self.service.sessions() as session, session.begin():
            token = await HubRepository(session).decrypt(
                run["credential"], self.service.settings.agent_hub_credential_key.get_secret_value()
            )
        identity = await verify_access_token(Request({"type": "http", "app": self.app}), token)
        if any(
            getattr(identity, k) != run["identity"][k] for k in ("issuer", "subject", "client_id")
        ):
            raise ValueError("EXECUTION_IDENTITY_MISMATCH")
        await self.service.get_run(identity, run["id"])
        final = None
        async for item in self.adapter.stream(run, token):
            if item["kind"] == "activity":
                await self.activity(run["id"], item)
            elif item["kind"] == "error":
                raise HubError(503, "LANGFLOW_EXECUTION_FAILED", "Langflow chưa hoàn tất yêu cầu.")
            else:
                final = item
        if final is None:
            raise ValueError("MISSING_RESULT")
        result = from_langflow(final["result"], run["id"])
        # Receipts are restricted again at publication; the model never supplies these ACLs.
        resources = final["resources"]
        if set(resources) != {"request_ids", "order_ids", "document_references"}:
            raise ValueError("INVALID_RESOURCE_REFERENCES")
        resources = merge_resources(run["resources"], resources)
        async with self.service.sessions() as session, session.begin():
            policy = await self.service.authorize(session, identity)
            await self.service.check_resources(policy, {**run, "resources": resources})
        return result, resources

    async def process(self, run: dict) -> None:
        task = asyncio.create_task(self.execute(run))
        result, resources = None, None
        try:
            async with asyncio.timeout(self.service.settings.langflow_timeout_seconds):
                while True:
                    done, _ = await asyncio.wait({task}, timeout=1)
                    if done:
                        result, resources = task.result()
                        break
                    async with self.service.sessions() as session:
                        current = await HubRepository(session).one(
                            db.runs, db.runs.c.id == run["id"]
                        )
                    if current["status"] == "cancelling":
                        result = empty_result(run["id"], "cancelled")
                        break
                    if current["status"] not in ACTIVE:
                        return
        except asyncio.CancelledError:
            # Shutdown keeps the durable deadline; recovery never retries a paid call.
            raise
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, (HubError, CoreError))
                else (
                    "EXECUTION_TIMEOUT"
                    if isinstance(exc, (TimeoutError, httpx.TimeoutException))
                    else "EXECUTION_FAILED"
                )
            )
            result = failed_result(
                run["id"], code, "Lượt xử lý chưa hoàn tất. Hệ thống không tự chạy lại."
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        async with self.service.sessions() as session, session.begin():
            repo = HubRepository(session)
            current = await repo.one(db.runs, db.runs.c.id == run["id"], lock=True)
            await self.service.finish(repo, current, result, resources)

    async def run(self) -> None:
        while True:
            try:
                await self.recover()
                run = await self.claim()
                if run:
                    await self.process(run)
                else:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Do not log exception/SQL arguments which may contain an encrypted credential.
                logger.error("Agent Hub worker iteration failed; durable runs retained")
                await asyncio.sleep(5)
