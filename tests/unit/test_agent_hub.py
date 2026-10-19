# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# All HTTP is MockTransport/ASGI; SQLite is a policy/state test, not a PG lock certification.
import io
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from jsonschema.exceptions import ValidationError
from sqlalchemy.dialects.postgresql import JSONB
from test_chat_backend import ISSUER, chat_case, configuration  # noqa: F401

from flowbridge.agent_hub.activities import interrupted_spans, validate_activity
from flowbridge.agent_hub.contract import HubError, document, validate
from flowbridge.agent_hub.projection import from_langflow, validate_result
from flowbridge.agent_hub.repository import HubRepository
from flowbridge.agent_hub.service import HubService
from flowbridge.agent_hub.worker import HubWorker
from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.main import create_app
from flowbridge.models import agent_hub as db
from flowbridge.models.api_idempotency import ApiIdempotency
from flowbridge.shared.errors import CoreError
from flowbridge.supplier_assistant.repository import table

ROOT = Path(__file__).resolve().parents[2]
ALICE = VerifiedIdentity(ISSUER, "alice", "web")
BOB = VerifiedIdentity(ISSUER, "bob", "web")
KEY = "test-idempotency-key-0001"


def fixture_result(name="offers"):
    return json.loads(
        (ROOT / f"langflow/examples/frontend_response/{name}.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("name", ["offers", "empty", "failed"])
def test_actual_graph_fixtures_project_without_model_or_prose_parsing(name):
    raw = fixture_result(name)
    before = deepcopy(raw)
    result = from_langflow(raw, "run_projected")
    validate_result(result)
    assert raw == before
    assert result["run_id"] == "run_projected"
    if name == "offers":
        assert result["datasets"][0]["rows"] == raw["datasets"][0]["rows"]
        assert result["datasets"][0]["context"]["as_of"] is None
        assert result["datasets"][0]["columns"][-1]["scale"] == 4
        assert any(w["code"] == "AS_OF_UNKNOWN" for w in result["warnings"])


def test_partial_and_questions_are_explicit_not_guessed_from_text():
    raw = fixture_result("empty")
    raw.update(status="partial", questions=["Chọn sản phẩm nào?"], error=None)
    result = from_langflow(raw, "run_clarify")
    assert result["status"] == "needs_input"
    assert result["questions"][0]["id"] == "q_1"
    assert result["questions"][0]["kind"] == "text"
    assert any(w["code"] == "PARTIAL_RESULT" for w in result["warnings"])


@pytest.mark.parametrize(
    "mutation",
    ["missing_source", "extra_cell", "wrong_type", "null_chart", "wrong_unit", "duplicate_label"],
)
def test_numeric_provenance_and_chart_semantics_fail_closed(mutation):
    result = from_langflow(fixture_result(), "run_invalid")
    dataset = result["datasets"][0]
    if mutation == "missing_source":
        result["sources"] = []
    elif mutation == "extra_cell":
        dataset["rows"][0]["injected"] = 100
    elif mutation == "wrong_type":
        dataset["rows"][0]["current_unit_price"] = 10.0
    elif mutation == "null_chart":
        dataset["rows"][0]["current_unit_price"] = None
    elif mutation == "wrong_unit":
        result["charts"][0]["y_unit"] = "%"
    else:
        dataset["rows"][1]["supplier_name"] = dataset["rows"][0]["supplier_name"]
    with pytest.raises((ValueError, KeyError)):
        validate_result(result)


def test_all_contract_examples_remain_valid():
    for name in ("delivery-result", "supplier-comparison", "needs-input", "failed", "empty-result"):
        result = json.loads(
            (ROOT / f"contracts/agent-hub/examples/{name}.json").read_text(encoding="utf-8")
        )
        validate_result(result)


def activity(sequence, kind, data):
    return {
        "schema_version": "1.0",
        "id": f"run_test:{sequence}",
        "sequence": sequence,
        "run_id": "run_test",
        "type": kind,
        "occurred_at": "2026-10-19T00:00:00+00:00",
        "data": data,
    }


def test_activity_correlates_failures_and_closes_only_unfinished_steps():
    root = activity(
        1,
        "agent.started",
        {
            "span_id": "central",
            "parent_span_id": None,
            "agent": "central",
            "invoked_by": None,
            "role": "coordinator",
            "label": "Trung tâm",
        },
    )
    data = {
        "span_id": "rag",
        "parent_span_id": "central",
        "agent": "knowledge",
        "invoked_by": "central",
        "role": "primary",
        "label": "Tri thức",
    }
    rag = activity(2, "agent.started", data)
    tool = activity(
        3,
        "tool.started",
        {
            "span_id": "search",
            "parent_span_id": "rag",
            "agent": "knowledge",
            "tool": "knowledge_search_supplier",
            "label": "Tìm tài liệu",
        },
    )
    validate_activity(root, [], ["knowledge"])
    validate_activity(rag, [root], ["knowledge"])
    validate_activity(tool, [root, rag], ["knowledge"])
    with pytest.raises(ValueError, match="UNSELECTED_PRIMARY_AGENT"):
        validate_activity(rag, [root], ["prediction"])
    now = datetime(2026, 10, 19, 0, 0, 3, 125000, tzinfo=UTC)
    closed = interrupted_spans([root, rag, tool], "run_test", "failed", now)
    assert [e["data"]["span_id"] for e in closed] == ["search", "rag", "central"]
    assert all(e["data"]["duration_ms"] == 3125 for e in closed)
    assert closed[0]["data"]["error"]["code"] == "STEP_INTERRUPTED"
    events = [root, rag, tool]
    for index, e in enumerate(closed, 4):
        wrapped = activity(index, e["type"], e["data"])
        validate(wrapped, "RunEvent")
        validate_activity(wrapped, events, ["knowledge"])
        events.append(wrapped)
    assert interrupted_spans(events, "run_test", "failed", now) == []
    without_error = deepcopy(events[-1])
    del without_error["data"]["error"]
    with pytest.raises(ValidationError):
        validate(without_error, "RunEvent")
    wrong_success = deepcopy(events[-1])
    wrong_success["data"]["outcome"] = "success"
    with pytest.raises(ValidationError):
        validate(wrong_success, "RunEvent")


@pytest.fixture
def hub_case(request, monkeypatch):
    case = request.getfixturevalue("chat_case")
    with case.engine.connect() as connection:
        connection.connection.create_function("pg_try_advisory_xact_lock", 1, lambda _: 1)
    metadata = sa.MetaData()
    targets = (
        db.conversations,
        db.runs,
        db.messages,
        db.events,
        db.cursors,
        ApiIdempotency.__table__,
    )
    for source in targets:
        target = source.to_metadata(metadata)
        for constraint in list(target.constraints):
            if isinstance(constraint, (sa.ForeignKeyConstraint, sa.CheckConstraint)):
                target.constraints.remove(constraint)
        target.foreign_keys.clear()
        for column in target.c:
            column.foreign_keys.clear()
            if isinstance(column.type, JSONB):
                column.type = sa.JSON()
        target.indexes.clear()
    metadata.create_all(case.engine)
    settings = configuration(
        agent_hub_enabled=True,
        agent_hub_worker_enabled=False,
        agent_hub_credential_key="offline-test-encryption-key-" + "x" * 32,
    )
    app = create_app(settings)
    app.state.resources = case.app.state.resources
    case.app = app
    case.service = HubService(case.sessions, case.http, settings)
    original_rows = HubRepository.rows

    async def rows(self, *args, **kwargs):
        result = await original_rows(self, *args, **kwargs)
        for row in result:
            for key, value in row.items():
                if isinstance(value, datetime) and value.tzinfo is None:
                    row[key] = value.replace(tzinfo=UTC)
        return result

    async def encrypt(self, token, key):
        assert token == "alice-token"
        assert len(key) >= 32
        return b"opaque-fixture-ciphertext"

    async def decrypt(self, cipher, key):
        assert cipher == b"opaque-fixture-ciphertext"
        return "alice-token"

    monkeypatch.setattr(HubRepository, "rows", rows)
    monkeypatch.setattr(HubRepository, "encrypt", encrypt)
    monkeypatch.setattr(HubRepository, "decrypt", decrypt)
    return case


async def create(case, selected=None):
    response = await case.service.create_conversation(
        ALICE,
        {
            "title": "Nhà cung cấp",
            "selected_agents": selected or [],
        },
        KEY,
    )
    return response["body"]["id"]


def request_body(**overrides):
    return {
        "message": "Tôi muốn mua băng keo",
        "conversation_revision": 1,
        "timezone": "Asia/Ho_Chi_Minh",
        **overrides,
    }


@pytest.mark.asyncio
async def test_admission_is_durable_idempotent_and_selection_snapshot_isolated(hub_case):
    case = hub_case
    cid = await create(case, ["database"])
    first = await case.service.create_run(ALICE, cid, request_body(), KEY, "alice-token")
    rid = first["body"]["run_id"]
    assert first["status"] == 202
    assert not case.runs
    assert await case.service.create_run(ALICE, cid, request_body(), KEY, "alice-token") == first
    with pytest.raises(HubError, match="IDEMPOTENCY_CONFLICT"):
        await case.service.create_run(
            ALICE, cid, request_body(message="different"), KEY, "alice-token"
        )
    with pytest.raises(HubError, match="CONVERSATION_BUSY"):
        await case.service.create_run(ALICE, cid, request_body(), KEY + "2", "alice-token")
    await case.service.update_conversation(ALICE, cid, {"selected_agents": []}, '"1"')
    run = await case.service.get_run(ALICE, rid)
    assert run["metadata"]["selected_agents"] == ["database"]
    assert run["credential"] == b"opaque-fixture-ciphertext"
    with pytest.raises(HubError, match="NOT_FOUND"):
        await case.service.get_run(BOB, rid)
    events, _ = await case.service.read_events(ALICE, rid, None)
    assert [e["type"] for e in events] == ["run.queued"]
    assert len((await case.service.page(ALICE, "messages", cid, None, 20))["items"]) == 2


@pytest.mark.asyncio
async def test_cancellation_and_replay_do_not_execute_or_publish_again(hub_case):
    case = hub_case
    cid = await create(case)
    admitted = await case.service.create_run(ALICE, cid, request_body(), KEY, "alice-token")
    rid = admitted["body"]["run_id"]
    cancel = await case.service.cancel(ALICE, rid, KEY)
    assert cancel["body"]["status"] == "cancelled"
    assert await case.service.cancel(ALICE, rid, KEY) == cancel
    run = await case.service.get_run(ALICE, rid)
    assert run["credential"] is None
    assert run["metadata"]["cancellation_requested"]
    events, _ = await case.service.read_events(ALICE, rid, None)
    assert [e["type"] for e in events] == ["run.queued", "run.cancelled"]
    assert (await case.service.read_events(ALICE, rid, events[0]["id"]))[0] == events[1:]
    with pytest.raises(HubError, match="INVALID_EVENT_CURSOR"):
        await case.service.read_events(ALICE, rid, "run_other:1")


@pytest.mark.asyncio
async def test_worker_publishes_exact_chart_result_and_never_retries(hub_case):
    case = hub_case
    cid = await create(case)
    rid = (await case.service.create_run(ALICE, cid, request_body(), KEY, "alice-token"))["body"][
        "run_id"
    ]
    worker = HubWorker(case.app)
    calls = []

    async def stream(run, token):
        calls.append(run["id"])
        assert token == "alice-token"
        yield {
            "kind": "result",
            "result": fixture_result(),
            "resources": {"request_ids": [], "order_ids": [], "document_references": []},
        }

    worker.adapter.stream = stream
    claimed = await worker.claim()
    assert claimed["id"] == rid
    assert await worker.claim() is None
    await worker.process(claimed)
    result = await case.service.get_run(ALICE, rid)
    assert result["status"] == "completed"
    assert result["result"]["charts"]
    assert result["credential"] is None
    assert len(calls) == 1
    assert await worker.claim() is None
    await case.service.create_run(ALICE, cid, request_body(), KEY, "alice-token")
    assert await worker.claim() is None
    with pytest.raises(HubError, match="RUN_ALREADY_TERMINAL"):
        await case.service.cancel(ALICE, rid, KEY)


@pytest.mark.asyncio
async def test_interrupted_execution_fails_without_reexecuting(hub_case):
    case = hub_case
    cid = await create(case)
    rid = (await case.service.create_run(ALICE, cid, request_body(), KEY, "alice-token"))["body"][
        "run_id"
    ]
    worker = HubWorker(case.app)
    await worker.claim()
    async with case.sessions() as session, session.begin():
        await HubRepository(session).update(
            db.runs, rid, {"deadline": datetime.now(UTC) - timedelta(minutes=1)}
        )
    await worker.recover()
    run = await case.service.get_run(ALICE, rid)
    assert run["result"]["error"]["code"] == "EXECUTION_INTERRUPTED"
    assert run["credential"] is None
    assert await worker.claim() is None
    assert not case.runs


@pytest.mark.asyncio
async def test_worker_failure_preserves_tool_error_and_sse_terminal_order(hub_case):
    case = hub_case
    cid = await create(case, ["database"])
    rid = (await case.service.create_run(ALICE, cid, request_body(), KEY, "alice-token"))["body"][
        "run_id"
    ]
    worker = HubWorker(case.app)

    async def stream(run, token):
        yield {
            "kind": "activity",
            **{
                k: v
                for k, v in activity(
                    1,
                    "agent.started",
                    {
                        "span_id": "central",
                        "parent_span_id": None,
                        "agent": "central",
                        "invoked_by": None,
                        "role": "coordinator",
                        "label": "Trung tâm",
                    },
                ).items()
                if k in {"type", "data", "occurred_at"}
            },
        }
        raise httpx.ReadTimeout("sensitive upstream detail must not be exposed")

    worker.adapter.stream = stream
    await worker.process(await worker.claim())
    run = await case.service.get_run(ALICE, rid)
    assert run["result"]["error"]["code"] == "EXECUTION_TIMEOUT"
    events, _ = await case.service.read_events(ALICE, rid, None)
    assert [e["type"] for e in events][-2:] == ["agent.completed", "run.failed"]
    assert events[-2]["data"]["error"]["code"] == "STEP_INTERRUPTED"
    assert "sensitive upstream" not in json.dumps(events) + json.dumps(run["result"])
    assert await worker.claim() is None


@pytest.mark.asyncio
async def test_clarification_rejects_foreign_questions_and_preserves_history(hub_case):
    case = hub_case
    cid = await create(case)
    rid = (await case.service.create_run(ALICE, cid, request_body(), KEY, "alice-token"))["body"][
        "run_id"
    ]
    raw = fixture_result("empty")
    raw.update(status="needs_input", questions=["Số lượng bao nhiêu?"])
    async with case.sessions() as session, session.begin():
        repo = HubRepository(session)
        run = await repo.one(db.runs, db.runs.c.id == rid, lock=True)
        await case.service.finish(repo, run, from_langflow(raw, rid))
    with pytest.raises(HubError, match="UNKNOWN_QUESTION"):
        await case.service.create_run(
            ALICE,
            cid,
            request_body(response_to_run_id=rid, question_answers={"fake": "100"}),
            KEY + "2",
            "alice-token",
        )
    response = await case.service.create_run(
        ALICE,
        cid,
        request_body(response_to_run_id=rid, question_answers={"q_1": "100"}),
        KEY + "2",
        "alice-token",
    )
    run = await case.service.get_run(ALICE, response["body"]["run_id"])
    assert run["input"]["history"][0]["questions"][0]["id"] == "q_1"


@pytest.mark.asyncio
async def test_history_resources_remain_authorized_after_followup(hub_case):
    case = hub_case
    cid = await create(case)
    rid = (await case.service.create_run(ALICE, cid, request_body(), KEY, "alice-token"))["body"][
        "run_id"
    ]
    resources = {
        "request_ids": [str(case.request)],
        "order_ids": [],
        "document_references": [],
    }
    async with case.sessions() as session, session.begin():
        repo = HubRepository(session)
        run = await repo.one(db.runs, db.runs.c.id == rid, lock=True)
        await case.service.finish(repo, run, from_langflow(fixture_result("empty"), rid), resources)
    followup = await case.service.create_run(
        ALICE, cid, request_body(message="Giải thích thêm"), KEY + "2", "alice-token"
    )
    next_id = followup["body"]["run_id"]
    assert (await case.service.get_run(ALICE, next_id))["resources"] == resources
    with case.engine.begin() as connection:
        connection.execute(
            sa.update(table("purchase_requests"))
            .where(table("purchase_requests").c.request_id == case.request)
            .values(requested_by=case.other)
        )
    with pytest.raises(CoreError):
        await case.service.get_run(ALICE, next_id)
    with pytest.raises(CoreError):
        await case.service.read_events(ALICE, next_id, None)


@pytest.mark.asyncio
async def test_http_openapi_errors_etag_cursor_and_sse(hub_case):
    case = hub_case
    paths = case.app.openapi()["paths"]
    assert set(document()["paths"]) <= paths.keys()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app), base_url="http://api.test"
    ) as client:
        root = "/api/v1/agent-hub"
        headers = {"Authorization": "Bearer alice-token", "Idempotency-Key": KEY}
        no_auth = await client.get(root + "/agents")
        assert no_auth.status_code == 401
        validate(no_auth.json(), "Problem")
        response = await client.post(
            root + "/conversations", headers=headers, json={"title": "Hub", "selected_agents": []}
        )
        assert response.status_code == 201, response.text
        cid = response.json()["id"]
        assert response.headers["etag"] == '"1"'
        path = root + "/conversations/" + cid
        assert (await client.patch(path, headers=headers, json={"title": "new"})).status_code == 428
        assert (
            await client.patch(path, headers={**headers, "If-Match": '"0"'}, json={"title": "new"})
        ).status_code == 412
        assert (
            await client.post(
                path + "/runs", headers=headers, json=request_body(timezone="invented")
            )
        ).status_code == 422
        response = await client.post(path + "/runs", headers=headers, json=request_body())
        assert response.status_code == 202, response.text
        run_url = response.json()["result_url"]
        assert (await client.get(run_url, headers=headers)).json()["status"] == "queued"
        assert (await client.post(run_url + "/cancel", headers=headers)).status_code == 200
        sse = await client.get(run_url + "/events", headers=headers)
        assert "event: run.cancelled" in sse.text
        assert sse.headers["cache-control"] == "no-store, no-transform"
        history = await client.get(path + "/messages?limit=1", headers=headers)
        cursor = history.json()["next_cursor"]
        second = await client.get(path + "/messages", params={"cursor": cursor}, headers=headers)
        assert second.json()["items"][0]["role"] == "assistant"
        assert (
            await client.get(run_url, headers={"Authorization": "Bearer bob-token"})
        ).status_code == 404


def test_hub_migration_is_additive_and_offline(monkeypatch):
    monkeypatch.setenv("FLOWBRIDGE_DATABASE_URL", "postgresql+asyncpg://offline@localhost/offline")
    config = Config(str(ROOT / "alembic.ini"), output_buffer=io.StringIO())
    command.upgrade(config, "0005_chat_projection:head", sql=True)
    sql = config.output_buffer.getvalue()
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in sql
    assert "CREATE UNIQUE INDEX uq_hub_runs_active" in sql
    assert "CREATE TABLE chat_sessions" not in sql
    assert "ALTER TABLE purchase_requests" not in sql
    for name in ("hub_conversations", "hub_runs", "hub_messages", "hub_events", "hub_cursors"):
        assert f"CREATE TABLE {name}" in sql
        assert f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY" in sql
