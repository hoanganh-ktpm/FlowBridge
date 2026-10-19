# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Short DB transactions surround one synchronous external execution; no automatic model retries.
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

import httpx
import sqlalchemy as sa
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.contract import validate_dto
from flowbridge.supplier_assistant.policy import SupplierPolicy, require
from flowbridge.supplier_assistant.repository import SupplierRepository, table


class FlowReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=6000)
    sources: list[dict[str, Any]] = Field(max_length=100)
    warnings: list[str] = Field(max_length=100)
    needs_clarification: bool
    request_ids: list[UUID] = Field(max_length=1000)
    order_ids: list[UUID] = Field(max_length=1000)


class LangflowChatAdapter:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self.http, self.settings = http, settings

    async def ai_token(self) -> str:
        settings = self.settings
        if not (settings.langflow_ai_client_id and settings.langflow_ai_client_secret):
            return ""
        try:
            response = await self.http.post(
                settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/token",
                data={"grant_type": "client_credentials"},
                auth=httpx.BasicAuth(
                    settings.langflow_ai_client_id,
                    settings.langflow_ai_client_secret.get_secret_value(),
                ),
                follow_redirects=False,
            )
            response.raise_for_status()
            value = response.json()
            token = value.get("access_token")
            if (
                not isinstance(token, str)
                or not token
                or value.get("token_type", "").lower() != "bearer"
            ):
                raise ValueError
            return token
        except httpx.TimeoutException:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        except (httpx.HTTPError, ValueError, AttributeError):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None

    async def run(
        self,
        message: str,
        access_token: str,
        execution_id: UUID,
        job_context: UUID | None,
        request_id: UUID | None = None,
    ) -> FlowReply:
        settings = self.settings
        headers = {
            "x-api-key": settings.langflow_api_key.get_secret_value(),
            "X-FlowBridge-Bridge-Secret": settings.langflow_bridge_secret.get_secret_value(),
            "X-FlowBridge-Access-Token": access_token,
        }
        if request_id:
            headers["X-FlowBridge-Request-Context"] = str(request_id)
        # Fail before execution if a stock/editor-only Langflow process is on this port.
        try:
            probe = await self.http.get(
                settings.langflow_url + "/_ops/flowbridge",
                headers={
                    "X-FlowBridge-Bridge-Secret": settings.langflow_bridge_secret.get_secret_value()
                },
                follow_redirects=False,
            )
            require(
                probe.status_code == 200
                and probe.json()
                == {"protocol": "flowbridge-caller-v1", "flow_id": settings.langflow_flow_id},
                ErrorCode.UPSTREAM_UNAVAILABLE,
            )
        except httpx.TimeoutException:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        except (httpx.HTTPError, ValueError):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        if job_context:
            token = await self.ai_token()
            if token:
                headers.update(
                    {"X-FlowBridge-AI-Token": token, "X-FlowBridge-Job-Context": str(job_context)}
                )
        try:
            response = await self.http.post(
                settings.langflow_url + "/api/v1/run/" + settings.langflow_flow_id,
                headers=headers,
                json={
                    "input_value": message,
                    "input_type": "chat",
                    "output_type": "chat",
                    "session_id": str(execution_id),
                },
                timeout=httpx.Timeout(settings.langflow_timeout_seconds, connect=5),
                follow_redirects=False,
            )
            if response.status_code in {401, 403}:
                # Only the bridge's sanitized response proves a tool's user-token rejection.
                # Langflow's own auth rejection means service credentials are misconfigured.
                if response.headers.get("x-flowbridge-tool-denied") == str(response.status_code):
                    raise CoreError(
                        ErrorCode.UNAUTHENTICATED
                        if response.status_code == 401
                        else ErrorCode.FORBIDDEN
                    )
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            if response.status_code == 429:
                raise CoreError(ErrorCode.RATE_LIMITED, retry_after=1)
            if response.status_code == 504:
                raise CoreError(ErrorCode.UPSTREAM_TIMEOUT)
            response.raise_for_status()
            return FlowReply.model_validate(response.json())
        except httpx.TimeoutException:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        except (httpx.HTTPError, ValueError):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None


class ChatService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        http: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self.sessions, self.settings = sessions, settings
        self.adapter = LangflowChatAdapter(http, settings)
        self.knowledge_service = None

    async def policy(self, session: AsyncSession, identity: VerifiedIdentity) -> SupplierPolicy:
        require(identity.account_type == "human", ErrorCode.FORBIDDEN)
        policy = SupplierPolicy(SupplierRepository(session))
        await policy.authenticate(identity)
        # Chat reads business evidence; this does not grant business write operations.
        policy.authorize("EMPLOYEE", read=True)
        return policy

    async def session_scope(
        self, repo: SupplierRepository, policy: SupplierPolicy, identifier: UUID
    ) -> dict:
        target = table("chat_sessions")
        row = await repo.one(
            "chat_sessions",
            target.c.session_id == identifier,
            target.c.owner_id == policy.user["user_id"],
        )
        if row["request_id"]:
            await policy.request(row["request_id"])
        return row

    async def check_resources(self, policy: SupplierPolicy, turn: dict) -> None:
        for identifier in turn["resources"].get("request_ids", []):
            await policy.request(identifier)
        for identifier in turn["resources"].get("order_ids", []):
            order = await policy.repository.get("purchase_orders", identifier)
            await policy.request(order["request_id"])
        if turn.get("result", {}).get("sources"):
            # Revalidate original chunk/version visibility on replay, including withdrawals.
            require(bool(turn["job_context"]), ErrorCode.FORBIDDEN)
            service = self.knowledge_service
            require(service is not None, ErrorCode.UPSTREAM_UNAVAILABLE)
            # Ownership of the job's request is checked as the human, not the AI service.
            job = await policy.repository.get("analysis_tasks", turn["job_context"])
            await policy.request(job["request_id"])
            # Build the same scope without impersonating a service principal.
            from flowbridge.supplier_assistant.knowledge.postgres_domain import PgScope

            request = await policy.request(job["request_id"])
            department = await policy.repository.get("departments", request["department_id"])
            require(department["status"] == "active", ErrorCode.FORBIDDEN)
            require(request["status"] != "cancelled", ErrorCode.REQUEST_CHANGED)
            require(job["input_revision"] == request["input_revision"], ErrorCode.REQUEST_CHANGED)
            require(job["status"] in {"queued", "running", "succeeded"}, ErrorCode.INVALID_STATE)
            require(job["feature_asof"] <= datetime.now(UTC), ErrorCode.REQUEST_CHANGED)

            async def scope() -> PgScope:
                return PgScope(job["feature_asof"], request["department_id"])

            for reference in turn["result"]["sources"]:
                from flowbridge.supplier_assistant.knowledge.models import KnowledgeError

                try:
                    await service.resolve(reference, scope)
                except KnowledgeError:
                    raise CoreError(ErrorCode.NOT_FOUND) from None

    async def reserve(self, identity: VerifiedIdentity, body: dict, key: str) -> tuple[dict, bool]:
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        async with self.sessions() as session, session.begin():
            policy = await self.policy(session, identity)
            repo = policy.repository
            owner = policy.user["user_id"]
            # Serialize a retry before deciding whether an external execution is allowed.
            await session.execute(
                sa.select(
                    sa.func.pg_advisory_xact_lock(
                        int.from_bytes(
                            hashlib.sha256(f"chat:{owner}:{key}".encode()).digest()[:8],
                            "big",
                            signed=True,
                        )
                    )
                )
            )
            turns = table("chat_turns")
            cached = await repo.rows(
                "chat_turns", turns.c.owner_id == owner, turns.c.idempotency_key == key
            )
            if cached:
                turn = cached[0]
                require(turn["request_hash"] == digest, ErrorCode.IDEMPOTENCY_CONFLICT)
                await self.session_scope(repo, policy, turn["session_id"])
                if turn["state"] == "running":
                    raise CoreError(ErrorCode.ANALYSIS_IN_PROGRESS)
                if turn["state"] == "failed":
                    raise CoreError(ErrorCode(turn["error_code"]))
                await self.check_resources(policy, turn)
                return turn, False
            if body.get("session_id"):
                identifier = UUID(body["session_id"])
                # One active turn per session; do not hold this lock while calling Langflow.
                sessions = table("chat_sessions")
                await repo.one(
                    "chat_sessions",
                    sessions.c.session_id == identifier,
                    sessions.c.owner_id == owner,
                    lock=True,
                )
                chat = await self.session_scope(repo, policy, identifier)
                if body.get("request_id"):
                    require(chat["request_id"] == UUID(body["request_id"]), ErrorCode.INVALID_STATE)
                running = await repo.rows(
                    "chat_turns",
                    turns.c.session_id == identifier,
                    turns.c.state == "running",
                    limit=1,
                )
                require(not running, ErrorCode.ANALYSIS_IN_PROGRESS)
            else:
                request_id = UUID(body["request_id"]) if body.get("request_id") else None
                if request_id:
                    await policy.request(request_id)
                chat = await repo.insert(
                    "chat_sessions",
                    {
                        "session_id": uuid4(),
                        "owner_id": owner,
                        "request_id": request_id,
                        "created_at": datetime.now(UTC),
                    },
                )
            job_context = None
            if chat["request_id"] and self.settings.rag_postgres_enabled:
                request = await policy.request(chat["request_id"])
                tasks = table("analysis_tasks")
                jobs = await repo.rows(
                    "analysis_tasks",
                    tasks.c.request_id == chat["request_id"],
                    tasks.c.input_revision == request["input_revision"],
                    tasks.c.status.in_(["queued", "running", "succeeded"]),
                    tasks.c.feature_asof <= datetime.now(UTC),
                    order_by=tasks.c.feature_asof.desc(),
                    limit=1,
                )
                if jobs and request["status"] != "cancelled":
                    job_context = jobs[0]["task_id"]
            turn = await repo.insert(
                "chat_turns",
                {
                    "turn_id": uuid4(),
                    "session_id": chat["session_id"],
                    "owner_id": owner,
                    "idempotency_key": key,
                    "request_hash": digest,
                    "state": "running",
                    "question": body["message"],
                    "resources": {},
                    "job_context": job_context,
                    "created_at": datetime.now(UTC),
                },
            )
            turn["bound_request_id"] = chat["request_id"]
            return turn, True

    async def send(
        self, identity: VerifiedIdentity, body: dict, key: str, access_token: str
    ) -> dict:
        require(self.settings.chat_enabled, ErrorCode.MODEL_UNAVAILABLE)
        turn, execute = await self.reserve(identity, body, key)
        if not execute:
            return turn["result"]
        try:
            async with asyncio.timeout(self.settings.langflow_timeout_seconds):
                reply = await self.adapter.run(
                    body["message"],
                    access_token,
                    turn["turn_id"],
                    turn["job_context"],
                    turn["bound_request_id"],
                )
            result = {
                "session_id": str(turn["session_id"]),
                **reply.model_dump(mode="json", exclude={"request_ids", "order_ids"}),
            }
            validate_dto(result, "ChatResponse")
            turn.update(
                result=result,
                resources=reply.model_dump(mode="json", include={"request_ids", "order_ids"}),
            )
            async with self.sessions() as session, session.begin():
                policy = await self.policy(session, identity)
                await self.session_scope(policy.repository, policy, turn["session_id"])
                await self.check_resources(policy, turn)
                await policy.repository.update(
                    "chat_turns",
                    turn["turn_id"],
                    {
                        "state": "succeeded",
                        "result": result,
                        "resources": turn["resources"],
                        "completed_at": datetime.now(UTC),
                    },
                )
            return result
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, CoreError)
                else ErrorCode.UPSTREAM_TIMEOUT
                if isinstance(exc, TimeoutError)
                else ErrorCode.UPSTREAM_UNAVAILABLE
            )
            # A failed reservation stays durable. The same key never causes a second paid run.
            async with self.sessions() as session, session.begin():
                await SupplierRepository(session).update(
                    "chat_turns",
                    turn["turn_id"],
                    {
                        "state": "failed",
                        "error_code": code.value,
                        "completed_at": datetime.now(UTC),
                    },
                )
            raise CoreError(code) from None

    async def history(
        self, identity: VerifiedIdentity, identifier: UUID, page: int, size: int
    ) -> dict:
        async with self.sessions() as session, session.begin():
            policy = await self.policy(session, identity)
            repo = policy.repository
            await self.session_scope(repo, policy, identifier)
            turns = table("chat_turns")
            condition = [turns.c.session_id == identifier, turns.c.state == "succeeded"]
            total = 2 * await repo.count("chat_turns", *condition)
            start = (page - 1) * size
            rows = await repo.rows(
                "chat_turns",
                *condition,
                offset=start // 2,
                limit=(size + start % 2 + 1) // 2,
                order_by=sa.ClauseList(turns.c.created_at, turns.c.turn_id),
            )
            messages = []
            for turn in rows:
                await self.check_resources(policy, turn)
                for role, content, stamp in (
                    ("user", turn["question"], turn["created_at"]),
                    ("assistant", turn["result"]["answer"], turn["completed_at"]),
                ):
                    messages.append(
                        {
                            "message_id": str(uuid5(turn["turn_id"], role)),
                            "session_id": str(identifier),
                            "role": role,
                            "content": content,
                            "created_at": stamp.isoformat(),
                        }
                    )
            result = {
                "items": messages[start % 2 : start % 2 + size],
                "page": page,
                "page_size": size,
                "total": total,
            }
            validate_dto(result, "ChatMessagePage")
            return result


async def handle_chat(
    request: Request, identity: VerifiedIdentity, operation: str, payload: dict, query: dict
) -> dict:
    resources = request.app.state.resources
    require(
        resources is not None and resources.sessions is not None, ErrorCode.UPSTREAM_UNAVAILABLE
    )
    service = ChatService(resources.sessions, resources.http, request.app.state.settings)
    service.knowledge_service = request.app.state.rag_postgres_service
    if operation == "getChatHistory":
        return await service.history(
            identity, UUID(request.path_params["session_id"]), query["page"], query["page_size"]
        )
    # authenticated_identity already rejects missing or duplicate Authorization headers.
    access_token = request.headers["authorization"].split(" ", 1)[1]
    return await service.send(identity, payload, request.headers["Idempotency-Key"], access_token)
