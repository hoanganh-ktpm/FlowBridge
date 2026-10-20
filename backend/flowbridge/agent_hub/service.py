# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowbridge.agent_hub.activities import interrupted_spans
from flowbridge.agent_hub.contract import HubError, check, validate
from flowbridge.agent_hub.projection import ACTIVE, empty_result, result_url, validate_result
from flowbridge.agent_hub.repository import HubRepository
from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.models import agent_hub as db
from flowbridge.models.api_idempotency import ApiIdempotency
from flowbridge.settings import Settings
from flowbridge.supplier_assistant.chat import ChatService
from flowbridge.supplier_assistant.policy import SupplierPolicy
from flowbridge.supplier_assistant.repository import table


def identifier(prefix: str) -> str:
    return prefix + "_" + uuid4().hex


def conversation_dto(row: dict) -> dict:
    result = {k: row[k] for k in ("id", "title", "selected_agents", "revision")}
    result.update({k: row[k].isoformat() for k in ("created_at", "updated_at")})
    result["request_id"] = str(row["request_id"]) if row["request_id"] else None
    return result


def canonical(value: dict) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def merge_resources(*values: dict) -> dict:
    combined = {"request_ids": [], "order_ids": [], "document_references": []}
    for value in values:
        for key in combined:
            for item in value.get(key, []):
                if item not in combined[key]:
                    combined[key].append(item)
    return combined


class HubService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        http: httpx.AsyncClient,
        settings: Settings,
        knowledge_service=None,
    ) -> None:
        self.sessions, self.http, self.settings = sessions, http, settings
        self.chat = ChatService(sessions, http, settings)
        self.chat.knowledge_service = knowledge_service

    async def authorize(self, session: AsyncSession, identity: VerifiedIdentity) -> SupplierPolicy:
        return await self.chat.policy(session, identity)

    async def conversation(
        self, repo: HubRepository, policy: SupplierPolicy, cid: str, *, lock=False
    ) -> dict:
        row = await repo.one(
            db.conversations,
            db.conversations.c.id == cid,
            db.conversations.c.owner_id == policy.user["user_id"],
            lock=lock,
        )
        if row["request_id"]:
            await policy.request(row["request_id"])
        return row

    async def run(
        self, repo: HubRepository, policy: SupplierPolicy, rid: str, *, lock=False
    ) -> dict:
        row = await repo.one(
            db.runs, db.runs.c.id == rid, db.runs.c.owner_id == policy.user["user_id"], lock=lock
        )
        await self.conversation(repo, policy, row["conversation_id"])
        await self.check_resources(policy, row)
        return row

    async def check_resources(self, policy: SupplierPolicy, run: dict) -> None:
        resources = run["resources"]
        await self.chat.check_resources(
            policy,
            {
                "resources": resources,
                "job_context": run["job_context"],
                "knowledge_as_of": run["accepted_at"],
                "result": {"sources": resources.get("document_references", [])},
            },
        )

    def catalog(self) -> dict:
        settings = self.settings
        items = []
        for agent, name, description, available in (
            ("database", "Dữ liệu", "Tra cứu sản phẩm, báo giá và chứng từ được cấp quyền.", True),
            (
                "prediction",
                "Dự đoán",
                "Xếp hạng nhà cung cấp và phân tích rủi ro.",
                settings.ml_enabled,
            ),
            (
                "knowledge",
                "Tri thức RAG",
                "Tra cứu tài liệu dùng chung đã xuất bản.",
                settings.rag_postgres_enabled,
            ),
        ):
            item = {"id": agent, "name": name, "description": description, "available": available}
            if not available:
                item["unavailable_reason"] = "configuration_unavailable"
            items.append(item)
        return {"items": items}

    async def search_knowledge(self, identity: VerifiedIdentity, body: dict) -> dict:
        from flowbridge.supplier_assistant.knowledge.models import KnowledgeError, RetrievalRequest
        from flowbridge.supplier_assistant.knowledge.postgres_domain import PgScope

        check(self.settings.rag_postgres_enabled, 503, "AGENT_UNAVAILABLE", "RAG chưa được bật.")
        service = self.chat.knowledge_service
        check(service is not None, 503, "SERVICE_UNAVAILABLE", "RAG chưa sẵn sàng.")
        query = RetrievalRequest.model_validate(body)
        cutoff = datetime.now(UTC)

        async def scope() -> PgScope:
            async with self.sessions() as session, session.begin():
                await self.authorize(session, identity)
            # No PR means no department authority. Only published global sources are visible.
            return PgScope(cutoff, supplier_id=query.supplier_id)

        await scope()
        try:
            return await service.search(query, scope)
        except KnowledgeError:
            raise HubError(503, "KNOWLEDGE_UNAVAILABLE", "Chưa tra cứu được tài liệu.") from None

    async def idempotent(
        self,
        repo: HubRepository,
        owner: UUID,
        path: str,
        key: str,
        body: dict,
        action: Callable[[], Awaitable[dict]],
    ) -> dict:
        scope = "hub:" + hashlib.sha256(f"{owner}:POST:{path}".encode()).hexdigest()
        digest = hashlib.sha256(canonical(body).encode()).hexdigest()
        await repo.advisory_lock(scope + ":" + key)
        cache = ApiIdempotency.__table__
        rows = await repo.rows(cache, cache.c.scope == scope, cache.c.key == key)
        if rows:
            check(
                rows[0]["request_hash"] == digest,
                409,
                "IDEMPOTENCY_CONFLICT",
                "Idempotency-Key đã được dùng với nội dung khác.",
            )
            return rows[0]["response"]
        result = await action()
        await repo.insert(
            cache,
            {
                "scope": scope,
                "key": key,
                "request_hash": digest,
                "response": result,
                "created_at": datetime.now(UTC),
            },
        )
        return result

    async def create_conversation(self, identity: VerifiedIdentity, body: dict, key: str) -> dict:
        validate(body, "CreateConversation")
        check(body["title"].strip(), 422, "VALIDATION_ERROR", "Tên hội thoại không được trống.")
        async with self.sessions() as session, session.begin():
            repo, policy = HubRepository(session), await self.authorize(session, identity)
            request_id = UUID(body["request_id"]) if body.get("request_id") else None
            if request_id:
                await policy.request(request_id)

            async def create():
                now = datetime.now(UTC)
                row = await repo.insert(
                    db.conversations,
                    {
                        "id": identifier("conv"),
                        "owner_id": policy.user["user_id"],
                        "request_id": request_id,
                        "title": body["title"],
                        "selected_agents": sorted(body["selected_agents"]),
                        "revision": 1,
                        "next_sequence": 1,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                return {
                    "status": 201,
                    "body": conversation_dto(row),
                    "headers": {
                        "ETag": '"1"',
                        "Location": f"/api/v1/agent-hub/conversations/{row['id']}",
                    },
                }

            response = await self.idempotent(
                repo, policy.user["user_id"], "conversations", key, body, create
            )
            await self.conversation(repo, policy, response["body"]["id"])
            return response

    async def get_conversation(self, identity: VerifiedIdentity, cid: str) -> dict:
        async with self.sessions() as session, session.begin():
            policy = await self.authorize(session, identity)
            return conversation_dto(await self.conversation(HubRepository(session), policy, cid))

    async def update_conversation(
        self, identity: VerifiedIdentity, cid: str, body: dict, etag: str | None
    ) -> dict:
        validate(body, "UpdateConversation")
        check(etag is not None, 428, "PRECONDITION_REQUIRED", "Cần gửi If-Match.")
        if "title" in body:
            check(body["title"].strip(), 422, "VALIDATION_ERROR", "Tên hội thoại không được trống.")
        if "selected_agents" in body:
            body = {**body, "selected_agents": sorted(body["selected_agents"])}
        async with self.sessions() as session, session.begin():
            repo, policy = HubRepository(session), await self.authorize(session, identity)
            row = await self.conversation(repo, policy, cid, lock=True)
            check(
                etag == f'"{row["revision"]}"', 412, "PRECONDITION_FAILED", "Hội thoại đã thay đổi."
            )
            if any(row[k] != v for k, v in body.items()):
                row = await repo.update(
                    db.conversations,
                    cid,
                    {
                        **body,
                        "revision": row["revision"] + 1,
                        "updated_at": datetime.now(UTC),
                    },
                )
            return conversation_dto(row)

    async def job_context(self, policy: SupplierPolicy, conversation: dict) -> UUID | None:
        if not conversation["request_id"] or not self.settings.rag_postgres_enabled:
            return None
        request = await policy.request(conversation["request_id"])
        tasks = table("analysis_tasks")
        jobs = await policy.repository.rows(
            "analysis_tasks",
            tasks.c.request_id == request["request_id"],
            tasks.c.input_revision == request["input_revision"],
            tasks.c.status.in_(["queued", "running", "succeeded"]),
            tasks.c.feature_asof <= datetime.now(UTC),
            order_by=tasks.c.feature_asof.desc(),
            limit=1,
        )
        return jobs[0]["task_id"] if jobs and request["status"] != "cancelled" else None

    async def check_answers(
        self, repo: HubRepository, policy: SupplierPolicy, cid: str, body: dict
    ) -> None:
        if "response_to_run_id" not in body:
            return
        parent = await self.run(repo, policy, body["response_to_run_id"])
        latest = await repo.rows(
            db.runs, db.runs.c.conversation_id == cid, order=sa.desc(db.runs.c.accepted_at), limit=1
        )
        check(
            parent["conversation_id"] == cid
            and parent["status"] == "needs_input"
            and latest
            and latest[0]["id"] == parent["id"],
            409,
            "INVALID_CLARIFICATION",
            "Lượt cần bổ sung đã thay đổi.",
        )
        questions = {q["id"]: q for q in parent["result"]["questions"]}
        answers = body["question_answers"]
        check(set(answers) <= questions.keys(), 422, "UNKNOWN_QUESTION", "Mã câu hỏi không hợp lệ.")
        check(
            all(q["id"] in answers for q in questions.values() if q["required"]),
            422,
            "MISSING_ANSWER",
            "Cần trả lời các câu hỏi bắt buộc.",
        )
        for key, answer in answers.items():
            q = questions[key]
            valid = True
            if q["kind"] == "single_choice":
                valid = answer in {option["value"] for option in q["options"]}
            elif q["kind"] == "text":
                valid = isinstance(answer, str) and bool(answer.strip())
            elif q["kind"] == "number":
                try:
                    valid = Decimal(str(answer)).is_finite()
                except Exception:
                    valid = False
            elif q["kind"] == "date":
                try:
                    date.fromisoformat(answer)
                except (TypeError, ValueError):
                    valid = False
            check(valid, 422, "INVALID_ANSWER", "Giá trị trả lời không phù hợp loại câu hỏi.")

    async def create_run(
        self, identity: VerifiedIdentity, cid: str, body: dict, key: str, token: str
    ) -> dict:
        validate(body, "CreateRun")
        try:
            timezone = ZoneInfo(body["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise HubError(422, "INVALID_TIMEZONE", "Timezone IANA không hợp lệ.") from None
        if "message" in body:
            check(body["message"].strip(), 422, "VALIDATION_ERROR", "Tin nhắn không được trống.")
        async with self.sessions() as session, session.begin():
            repo, policy = HubRepository(session), await self.authorize(session, identity)
            await self.conversation(repo, policy, cid)

            async def create():
                conversation = await self.conversation(repo, policy, cid, lock=True)
                check(
                    body["conversation_revision"] == conversation["revision"],
                    409,
                    "CONVERSATION_REVISION_CONFLICT",
                    "Hãy tải lại phiên bản hội thoại.",
                )
                active = await repo.rows(
                    db.runs, db.runs.c.conversation_id == cid, db.runs.c.status.in_(ACTIVE), limit=1
                )
                check(not active, 409, "CONVERSATION_BUSY", "Hội thoại đang có lượt xử lý.")
                available = {a["id"] for a in self.catalog()["items"] if a["available"]}
                check(
                    set(conversation["selected_agents"]) <= available,
                    503,
                    "AGENT_UNAVAILABLE",
                    "Agent được chọn chưa được cấu hình khả dụng.",
                )
                await self.check_answers(repo, policy, cid, body)
                job = await self.job_context(policy, conversation)
                check(
                    "knowledge" not in conversation["selected_agents"]
                    or not conversation["request_id"] or job,
                    503,
                    "KNOWLEDGE_CONTEXT_REQUIRED",
                    "RAG cần hội thoại gắn PR và analysis job hợp lệ.",
                )
                # Recheck all context before letting any past answer enter a fresh execution.
                previous = await repo.rows(
                    db.runs,
                    db.runs.c.conversation_id == cid,
                    order=sa.desc(db.runs.c.accepted_at),
                    limit=12,
                )
                history = []
                for old in reversed(previous):
                    await self.check_resources(policy, old)
                    history.append(
                        {
                            "message": old["input"]["request"],
                            "answer": old["result"]["answer"],
                            "questions": old["result"]["questions"],
                        }
                    )
                # Bound context by dropping whole old turns, not splitting user input.
                while len(canonical({"history": history})) > 32000:
                    history.pop(0)
                history_resources = merge_resources(
                    *(old["resources"] for old in previous[: len(history)])
                )
                rid, user_id, assistant_id = identifier("run"), identifier("msg"), identifier("msg")
                now = datetime.now(UTC)
                metadata = {
                    "run_id": rid,
                    "conversation_id": cid,
                    "user_message_id": user_id,
                    "assistant_message_id": assistant_id,
                    "status": "queued",
                    "conversation_revision": conversation["revision"],
                    "selected_agents": conversation["selected_agents"],
                    "used_agents": [],
                    "dependency_agents": [],
                    "accepted_at": now.isoformat(),
                    "started_at": None,
                    "finished_at": None,
                    "timezone": body["timezone"],
                    "business_date": now.astimezone(timezone).date().isoformat(),
                    "cancellation_requested": False,
                    "trace_id": rid,
                    "result_url": result_url(rid),
                    "events_url": result_url(rid) + "/events",
                    "events_expires_at": None,
                }
                identity_data = asdict(identity)
                identity_data["scopes"] = sorted(identity.scopes)
                run = await repo.insert(
                    db.runs,
                    {
                        "id": rid,
                        "conversation_id": cid,
                        "owner_id": policy.user["user_id"],
                        "status": "queued",
                        "metadata": metadata,
                        "result": empty_result(rid, "queued"),
                        "input": {
                            "request": body,
                            "history": history,
                            "request_id": str(conversation["request_id"])
                            if conversation["request_id"]
                            else None,
                        },
                        "identity": identity_data,
                        "credential": await repo.encrypt(
                            token, self.settings.agent_hub_credential_key.get_secret_value()
                        ),
                        "resources": history_resources,
                        "job_context": job,
                        "accepted_at": now,
                        "next_event": 1,
                    },
                )
                for offset, role, mid in ((0, "user", user_id), (1, "assistant", assistant_id)):
                    message = {
                        "id": mid,
                        "run_id": rid,
                        "sequence": conversation["next_sequence"] + offset,
                        "role": role,
                        "text": body.get("message", "") if role == "user" else "",
                        "question_answers": body.get("question_answers", {})
                        if role == "user"
                        else {},
                        "created_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                        "result_url": result_url(rid) if role == "assistant" else None,
                    }
                    await repo.insert(
                        db.messages,
                        {
                            "id": mid,
                            "run_id": rid,
                            "conversation_id": cid,
                            "sequence": message["sequence"],
                            "body": message,
                        },
                    )
                await repo.update(
                    db.conversations,
                    cid,
                    {"next_sequence": conversation["next_sequence"] + 2, "updated_at": now},
                )
                await repo.event(run, "run.queued")
                accepted = {
                    "run_id": rid,
                    "conversation_id": cid,
                    "message_id": user_id,
                    "status": "queued",
                    "result_url": result_url(rid),
                    "metadata_url": result_url(rid) + "/metadata",
                    "events_url": metadata["events_url"],
                }
                return {
                    "status": 202,
                    "body": accepted,
                    "headers": {"Location": result_url(rid), "Retry-After": "2"},
                }

            response = await self.idempotent(
                repo, policy.user["user_id"], cid + "/runs", key, body, create
            )
            await self.run(repo, policy, response["body"]["run_id"])
            return response

    async def get_run(self, identity: VerifiedIdentity, rid: str) -> dict:
        async with self.sessions() as session, session.begin():
            return await self.run(
                HubRepository(session), await self.authorize(session, identity), rid
            )

    async def finish(
        self,
        repo: HubRepository,
        run: dict,
        result: dict,
        resources: dict | None = None,
        used_agents: list | None = None,
    ) -> None:
        # Caller holds the run row lock; result and terminal event commit together.
        if run["status"] not in ACTIVE:
            return
        if run["status"] == "cancelling" and result["status"] != "failed":
            result = empty_result(run["id"], "cancelled")
        validate_result(result)
        now = datetime.now(UTC)
        observed = await repo.rows(
            db.events, db.events.c.run_id == run["id"], order=db.events.c.sequence
        )
        for event in interrupted_spans(
            [row["body"] for row in observed], run["id"], result["status"], now
        ):
            await repo.event(run, event["type"], event["data"])
        metadata = {
            **run["metadata"],
            "status": result["status"],
            "finished_at": now.isoformat(),
            "events_expires_at": (now + timedelta(hours=24)).isoformat(),
        }
        if used_agents is not None:
            metadata["used_agents"] = sorted(set(used_agents))
            metadata["dependency_agents"] = sorted(
                set(used_agents) - set(metadata["selected_agents"])
            )
        run.update(status=result["status"], metadata=metadata, result=result)
        await repo.update(
            db.runs,
            run["id"],
            {
                "status": run["status"],
                "metadata": metadata,
                "result": result,
                "credential": None,
                "deadline": None,
                "resources": resources if resources is not None else run["resources"],
            },
        )
        message = await repo.one(db.messages, db.messages.c.id == metadata["assistant_message_id"])
        await repo.update(
            db.messages,
            message["id"],
            {
                "body": {
                    **message["body"],
                    "text": result["answer"],
                    "updated_at": now.isoformat(),
                }
            },
        )
        await repo.event(run, "run." + run["status"])

    async def cancel(self, identity: VerifiedIdentity, rid: str, key: str) -> dict:
        async with self.sessions() as session, session.begin():
            repo, policy = HubRepository(session), await self.authorize(session, identity)
            # Same order as admission: conversation, then run; avoid cyclic row locks.
            initial = await self.run(repo, policy, rid)

            async def cancel_run():
                await self.conversation(repo, policy, initial["conversation_id"], lock=True)
                run = await self.run(repo, policy, rid, lock=True)
                check(
                    run["status"] in ACTIVE | {"cancelled"},
                    409,
                    "RUN_ALREADY_TERMINAL",
                    "Lượt xử lý đã kết thúc.",
                )
                if run["status"] in ACTIVE:
                    run["metadata"]["cancellation_requested"] = True
                    if run["status"] == "queued":
                        await self.finish(repo, run, empty_result(rid, "cancelled"))
                    elif run["status"] == "running":
                        run["status"] = "cancelling"
                        run["metadata"]["status"] = "cancelling"
                        await repo.update(
                            db.runs,
                            rid,
                            {
                                "status": "cancelling",
                                "metadata": run["metadata"],
                                "result": empty_result(rid, "cancelling"),
                            },
                        )
                        await repo.event(run, "run.cancelling")
                return {
                    "status": 200 if run["status"] == "cancelled" else 202,
                    "body": {"run_id": rid, "status": run["status"], "result_url": result_url(rid)},
                    "headers": {},
                }

            return await self.idempotent(
                repo, policy.user["user_id"], rid + "/cancel", key, {}, cancel_run
            )

    async def page(
        self, identity: VerifiedIdentity, kind: str, cid: str | None, cursor: str | None, limit: int
    ) -> dict:
        async with self.sessions() as session, session.begin():
            repo, policy = HubRepository(session), await self.authorize(session, identity)
            owner = policy.user["user_id"]
            scope = kind + ":" + (cid or "")
            if cid:
                await self.conversation(repo, policy, cid)
            target = {"conversations": db.conversations, "runs": db.runs, "messages": db.messages}[
                kind
            ]
            if cursor:
                snapshot = await repo.one(
                    db.cursors,
                    db.cursors.c.id == cursor,
                    db.cursors.c.owner_id == owner,
                    db.cursors.c.scope == scope,
                )
                check(
                    snapshot["expires_at"] > datetime.now(UTC),
                    410,
                    "CURSOR_EXPIRED",
                    "Cursor đã hết hạn.",
                )
                ids = snapshot["ids"]
            else:
                where = (
                    target.c.owner_id == owner
                    if kind == "conversations"
                    else target.c.conversation_id == cid
                )
                order = (
                    (sa.desc(target.c.updated_at), sa.desc(target.c.id))
                    if kind == "conversations"
                    else (
                        (sa.desc(target.c.accepted_at), sa.desc(target.c.id))
                        if kind == "runs"
                        else (target.c.sequence,)
                    )
                )
                ids = list(
                    (
                        await session.execute(
                            sa.select(target.c.id).where(where).order_by(*order).limit(10001)
                        )
                    ).scalars()
                )
                check(
                    len(ids) <= 10000,
                    413,
                    "SNAPSHOT_TOO_LARGE",
                    "Danh sách vượt giới hạn 10.000 bản ghi.",
                )
            items = []
            for item_id in ids[:limit]:
                if kind == "conversations":
                    items.append(conversation_dto(await self.conversation(repo, policy, item_id)))
                elif kind == "runs":
                    items.append((await self.run(repo, policy, item_id))["metadata"])
                else:
                    message = await repo.one(db.messages, db.messages.c.id == item_id)
                    await self.run(repo, policy, message["run_id"])
                    items.append(message["body"])
            next_cursor = None
            if len(ids) > limit:
                next_cursor = identifier("cursor")
                await repo.insert(
                    db.cursors,
                    {
                        "id": next_cursor,
                        "owner_id": owner,
                        "scope": scope,
                        "ids": ids[limit:],
                        "expires_at": datetime.now(UTC) + timedelta(hours=24),
                    },
                )
            return {"items": items, "next_cursor": next_cursor}

    async def read_events(
        self, identity: VerifiedIdentity, rid: str, last_id: str | None
    ) -> tuple[list, dict]:
        async with self.sessions() as session, session.begin():
            repo, policy = HubRepository(session), await self.authorize(session, identity)
            run = await self.run(repo, policy, rid)
            expiry = run["metadata"]["events_expires_at"]
            check(
                not expiry or datetime.fromisoformat(expiry) > datetime.now(UTC),
                410,
                "EVENT_CURSOR_EXPIRED",
                "Sự kiện đã hết hạn; hãy tải kết quả lượt xử lý.",
            )
            sequence = 0
            if last_id:
                prefix, _, suffix = last_id.rpartition(":")
                check(
                    prefix == rid and suffix.isdigit() and 0 < int(suffix) < run["next_event"],
                    400,
                    "INVALID_EVENT_CURSOR",
                    "Last-Event-ID không thuộc lượt xử lý.",
                )
                sequence = int(suffix)
            rows = await repo.rows(
                db.events,
                db.events.c.run_id == rid,
                db.events.c.sequence > sequence,
                order=db.events.c.sequence,
                limit=1000,
            )
            return [row["body"] for row in rows], run
