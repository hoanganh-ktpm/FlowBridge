# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional case storage; no Core/business repositories or source credentials."""

import hashlib
import json
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import IntegrityError

from .decision_models import cases, checks, events, participants, revisions


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def payload_check(value):
    if len(json.dumps(value, allow_nan=False).encode()) > 524288:
        raise HTTPException(422, "Decision revision exceeds the bounded evidence limit")


def command_key(key):
    if not key or not 1 <= len(key) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise HTTPException(422, "A bounded printable Idempotency-Key is required")


class DecisionRepository:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def visible(ctx):
        return sa.exists(
            sa.select(1)
            .select_from(participants)
            .where(
                participants.c.org_id == cases.c.org_id,
                participants.c.case_id == cases.c.id,
                participants.c.principal_id == ctx.owner_id,
            )
        )

    async def get(self, ctx, identifier, *, conn=None, lock=False):
        if conn is None:
            async with self.engine.connect() as connection:
                return await self.get(ctx, identifier, conn=connection)
        query = sa.select(cases).where(
            cases.c.id == identifier, cases.c.org_id == ctx.org_id, self.visible(ctx)
        )
        row = (
            (await conn.execute(query.with_for_update() if lock else query))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HTTPException(404, "Decision case not found")
        revision = (
            (
                await conn.execute(
                    sa.select(revisions).where(
                        revisions.c.org_id == ctx.org_id,
                        revisions.c.case_id == identifier,
                        revisions.c.number == row["current_revision"],
                    )
                )
            )
            .mappings()
            .one()
        )
        roster = (
            (
                await conn.execute(
                    sa.select(participants.c.principal_id, participants.c.role)
                    .where(
                        participants.c.org_id == ctx.org_id,
                        participants.c.case_id == identifier,
                    )
                    .order_by(participants.c.role, participants.c.principal_id)
                )
            )
            .mappings()
            .all()
        )
        return jsonable_encoder(
            {
                **dict(row),
                "revision": dict(revision),
                "participants": [dict(item) for item in roster],
            }
        )

    async def list(self, ctx):
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        sa.select(cases.c.id)
                        .where(cases.c.org_id == ctx.org_id, self.visible(ctx))
                        .order_by(cases.c.updated_at.desc(), cases.c.id)
                        .limit(101)
                    )
                )
                .scalars()
                .all()
            )
        return rows

    async def create(
        self, ctx, *, order_id, warehouse_id, simulation_id, title, roster, payload, key
    ):
        command_key(key)
        payload_check(payload)
        digest = fingerprint(
            {
                "simulation_id": str(simulation_id),
                "order_id": str(order_id),
                "warehouse_id": str(warehouse_id),
                "title": title,
                "roster": roster,
            }
        )
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                    {"scope": f"decision-create:{ctx.org_id}:{ctx.owner_id}:{key}"},
                )
                previous = (
                    (
                        await conn.execute(
                            sa.select(cases).where(
                                cases.c.org_id == ctx.org_id,
                                cases.c.coordinator_id == ctx.owner_id,
                                cases.c.initial_command_key == key,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if previous:
                    if previous["initial_command_hash"] != digest:
                        raise HTTPException(
                            409, "Idempotency key was used for a different case command"
                        )
                    return await self.get(ctx, previous["id"], conn=conn)
                identifier, revision_id = uuid4(), uuid4()
                await conn.execute(
                    cases.insert().values(
                        id=identifier,
                        org_id=ctx.org_id,
                        warehouse_id=warehouse_id,
                        order_id=order_id,
                        coordinator_id=ctx.owner_id,
                        title=title,
                        initial_command_key=key,
                        initial_command_hash=digest,
                    )
                )
                await conn.execute(
                    revisions.insert().values(
                        id=revision_id,
                        org_id=ctx.org_id,
                        case_id=identifier,
                        number=1,
                        simulation_run_id=simulation_id,
                        created_by=ctx.owner_id,
                        command_key=key,
                        command_hash=digest,
                        payload=payload,
                    )
                )
                await conn.execute(
                    participants.insert(),
                    [
                        {
                            "org_id": ctx.org_id,
                            "case_id": identifier,
                            "principal_id": UUID(item["principal_id"]),
                            "role": item["role"],
                        }
                        for item in roster
                    ],
                )
                await self.event(
                    conn,
                    ctx,
                    identifier,
                    revision_id,
                    "CASE_CREATED",
                    key,
                    {"number": 1, "simulation_run_id": str(simulation_id)},
                )
                return await self.get(ctx, identifier, conn=conn)
        except IntegrityError:
            raise HTTPException(
                409, "An active case or conflicting decision command already exists"
            ) from None

    async def revise(self, ctx, identifier, *, simulation_id, expected_version, payload, key):
        command_key(key)
        payload_check(payload)
        digest = fingerprint(
            {"simulation_id": str(simulation_id), "expected_version": expected_version}
        )
        async with self.engine.begin() as conn:
            case = await self.get(ctx, identifier, conn=conn, lock=True)
            if case["coordinator_id"] != str(ctx.owner_id):
                raise HTTPException(403, "Only the case coordinator can propose a new revision")
            previous = (
                (
                    await conn.execute(
                        sa.select(revisions).where(
                            revisions.c.org_id == ctx.org_id,
                            revisions.c.case_id == identifier,
                            revisions.c.created_by == ctx.owner_id,
                            revisions.c.command_key == key,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if previous:
                if previous["command_hash"] != digest:
                    raise HTTPException(409, "Idempotency key was used for a different revision")
                return await self.get(ctx, identifier, conn=conn)
            if (
                case["version"] != expected_version
                or not case["active"]
                or case["status"] not in {"DRAFT", "NEEDS_REVISION"}
            ):
                raise HTTPException(409, "Case changed or is no longer open for revision")
            number, revision_id = case["current_revision"] + 1, uuid4()
            await conn.execute(
                revisions.insert().values(
                    id=revision_id,
                    org_id=ctx.org_id,
                    case_id=identifier,
                    number=number,
                    simulation_run_id=simulation_id,
                    created_by=ctx.owner_id,
                    command_key=key,
                    command_hash=digest,
                    payload=payload,
                )
            )
            await conn.execute(
                cases.update()
                .where(cases.c.org_id == ctx.org_id, cases.c.id == identifier)
                .values(
                    version=case["version"] + 1,
                    current_revision=number,
                    status="DRAFT",
                    workflow_instance_ref=None,
                    updated_at=sa.func.now(),
                )
            )
            await self.event(
                conn,
                ctx,
                identifier,
                revision_id,
                "REVISION_CREATED",
                key,
                {"number": number, "simulation_run_id": str(simulation_id)},
            )
            return await self.get(ctx, identifier, conn=conn)

    async def timeline(self, ctx, identifier):
        await self.get(ctx, identifier)
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        sa.select(events)
                        .where(
                            events.c.org_id == ctx.org_id,
                            events.c.case_id == identifier,
                        )
                        .order_by(events.c.created_at, events.c.id)
                        .limit(501)
                    )
                )
                .mappings()
                .all()
            )
        return jsonable_encoder([dict(row) for row in rows])

    async def history(self, ctx, identifier):
        await self.get(ctx, identifier)
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        sa.select(revisions)
                        .where(
                            revisions.c.org_id == ctx.org_id,
                            revisions.c.case_id == identifier,
                        )
                        .order_by(revisions.c.number.desc())
                        .limit(101)
                    )
                )
                .mappings()
                .all()
            )
        return jsonable_encoder([dict(row) for row in rows])

    async def revisions_for(self, ctx, identifier, identifiers):
        await self.get(ctx, identifier)
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        sa.select(revisions).where(
                            revisions.c.org_id == ctx.org_id,
                            revisions.c.case_id == identifier,
                            revisions.c.id.in_(identifiers),
                        )
                    )
                )
                .mappings()
                .all()
            )
        if len(rows) != len(identifiers):
            raise HTTPException(409, "Incomplete decision history")
        return jsonable_encoder([dict(row) for row in rows])

    async def record_check(self, ctx, identifier, revision_id, outcome, payload):
        async with self.engine.begin() as conn:
            case = await self.get(ctx, identifier, conn=conn, lock=True)
            if case["revision"]["id"] != str(revision_id):
                raise HTTPException(409, "Case revision changed during validation")
            await conn.execute(
                checks.insert().values(
                    id=uuid4(),
                    org_id=ctx.org_id,
                    case_id=identifier,
                    revision_id=revision_id,
                    actor_id=ctx.owner_id,
                    outcome=outcome,
                    payload=payload,
                )
            )

    async def check_history(self, ctx, identifier, revision_id):
        await self.get(ctx, identifier)
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        sa.select(checks)
                        .where(
                            checks.c.org_id == ctx.org_id,
                            checks.c.case_id == identifier,
                            checks.c.revision_id == revision_id,
                        )
                        .order_by(checks.c.created_at.desc(), checks.c.id)
                        .limit(101)
                    )
                )
                .mappings()
                .all()
            )
        return {
            "items": jsonable_encoder([dict(row) for row in rows[:100]]),
            "has_more": len(rows) > 100,
        }

    async def observe_source(self, ctx, identifier, revision_id, source):
        """Inbox receipt and audit entry in one local transaction, across observers.

        Event arrival never changes case/engine status. Old versions are retained
        as observations rather than overwriting a projection with stale payloads.
        """
        payload_check(source)
        key = "source:" + fingerprint([source["producer"], source["id"]])
        async with self.engine.begin() as conn:
            case = await self.get(ctx, identifier, conn=conn, lock=True)
            if (
                case["revision"]["id"] != str(revision_id)
                or source["org_id"] != str(ctx.org_id)
                or source["warehouse_id"] != case["warehouse_id"]
            ):
                raise HTTPException(409, "Source observation belongs to another scope or revision")
            candidates = (
                (
                    await conn.execute(
                        sa.select(events).where(
                            events.c.org_id == ctx.org_id,
                            events.c.case_id == identifier,
                            events.c.event_type == "SOURCE_OBSERVED",
                            events.c.payload["producer"].astext == source["producer"],
                            sa.or_(
                                events.c.event_key == key,
                                sa.and_(
                                    events.c.payload["aggregate_id"].astext
                                    == source["aggregate_id"],
                                    events.c.payload["aggregate_version"].as_integer()
                                    == source["aggregate_version"],
                                    events.c.payload["event_type"].astext == source["event_type"],
                                ),
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            if candidates:
                if len(candidates) != 1 or candidates[0]["payload_hash"] != fingerprint(source):
                    raise HTTPException(
                        409, "Conflicting source event identity or aggregate version"
                    )
                return False
            await self.event(conn, ctx, identifier, revision_id, "SOURCE_OBSERVED", key, source)
            return True

    async def begin_submission(self, ctx, identifier, expected_version, revision_id, key):
        command_key(key)
        digest = fingerprint(
            {"expected_version": expected_version, "revision_id": str(revision_id)}
        )
        async with self.engine.begin() as conn:
            case = await self.get(ctx, identifier, conn=conn, lock=True)
            if case["coordinator_id"] != str(ctx.owner_id) or case["revision"]["id"] != str(
                revision_id
            ):
                raise HTTPException(403, "Only the current coordinator can submit this revision")
            event_key = "submit:" + fingerprint(key)
            previous = (
                (
                    await conn.execute(
                        sa.select(events).where(
                            events.c.org_id == ctx.org_id,
                            events.c.case_id == identifier,
                            events.c.actor_id == ctx.owner_id,
                            events.c.event_key == event_key,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if previous:
                if previous["payload_hash"] != digest:
                    raise HTTPException(409, "Submission key was used for a different version")
                return case
            if (
                case["status"] == "VALIDATING"
                and not case["workflow_instance_ref"]
                and case["version"] == expected_version
            ):
                await self.event(
                    conn,
                    ctx,
                    identifier,
                    revision_id,
                    "SUBMISSION_RESUMED",
                    event_key,
                    {"expected_version": expected_version, "revision_id": str(revision_id)},
                )
                return case
            if case["version"] != expected_version or case["status"] != "DRAFT":
                raise HTTPException(409, "Only the unchanged draft can be submitted")
            await self.event(
                conn,
                ctx,
                identifier,
                revision_id,
                "SUBMISSION_REQUESTED",
                event_key,
                {"expected_version": expected_version, "revision_id": str(revision_id)},
            )
            await conn.execute(
                cases.update()
                .where(cases.c.id == identifier, cases.c.org_id == ctx.org_id)
                .values(status="VALIDATING", version=case["version"] + 1, updated_at=sa.func.now())
            )
            return await self.get(ctx, identifier, conn=conn)

    async def workflow_projection(self, ctx, identifier, revision_id, instance_id, status):
        if status not in {
            "VALIDATING",
            "PENDING_APPROVAL",
            "NEEDS_REVISION",
            "STOPPED",
            "APPROVED",
        }:
            raise HTTPException(409, "Workflow completion requires verified business handoff proof")
        async with self.engine.begin() as conn:
            case = await self.get(ctx, identifier, conn=conn, lock=True)
            if case["revision"]["id"] != str(revision_id) or case["workflow_instance_ref"] not in {
                None,
                instance_id,
            }:
                raise HTTPException(409, "Workflow projection belongs to a different case revision")
            if case["workflow_instance_ref"] == instance_id and case["status"] == status:
                return case
            rank = {
                "DRAFT": -1,
                "VALIDATING": 0,
                "PENDING_APPROVAL": 1,
                "APPROVED": 2,
                "NEEDS_REVISION": 3,
                "STOPPED": 3,
            }
            if rank.get(case["status"], 4) > rank[status]:
                return case  # A slower observation cannot roll the same revision backwards.
            await conn.execute(
                cases.update()
                .where(cases.c.id == identifier, cases.c.org_id == ctx.org_id)
                .values(
                    status=status,
                    active=status != "STOPPED",
                    workflow_instance_ref=instance_id,
                    version=case["version"] + 1,
                    updated_at=sa.func.now(),
                )
            )
            await self.event(
                conn,
                ctx,
                identifier,
                revision_id,
                "WORKFLOW_OBSERVED",
                "workflow:" + uuid4().hex,
                {"instance_id": instance_id, "status": status},
            )
            return await self.get(ctx, identifier, conn=conn)

    async def record_workflow_task(self, ctx, identifier, revision_id, key, result):
        payload_check(result)
        event_key = "task:" + fingerprint(key)
        async with self.engine.begin() as conn:
            case = await self.get(ctx, identifier, conn=conn, lock=True)
            if case["revision"]["id"] != str(revision_id) or result.get(
                "actor_principal_id"
            ) != str(ctx.owner_id):
                raise HTTPException(409, "Task receipt belongs to a different revision or actor")
            previous = (
                (
                    await conn.execute(
                        sa.select(events).where(
                            events.c.org_id == ctx.org_id,
                            events.c.case_id == identifier,
                            events.c.actor_id == ctx.owner_id,
                            events.c.event_key == event_key,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if previous:
                if previous["payload_hash"] != fingerprint(result):
                    raise HTTPException(409, "Task command key conflicts with recorded outcome")
                return
            await self.event(
                conn, ctx, identifier, revision_id, "TASK_COMPLETED", event_key, result
            )

    async def complete_handoff(self, ctx, identifier, revision_id, proof):
        payload_check(proof)
        async with self.engine.begin() as conn:
            case = await self.get(ctx, identifier, conn=conn, lock=True)
            if (
                case["revision"]["id"] != str(revision_id)
                or case["workflow_instance_ref"] != proof["instance_id"]
                or proof["revision_id"] != str(revision_id)
                or proof["kind"] != "DECISION_COMPLETED"
            ):
                raise HTTPException(409, "Handoff proof belongs to another process or revision")
            if case["status"] == "COMPLETED":
                return case
            if case["status"] not in {"VALIDATING", "PENDING_APPROVAL", "APPROVED", "EXECUTING"}:
                raise HTTPException(409, "Case cannot close from this state")
            await conn.execute(
                cases.update()
                .where(cases.c.org_id == ctx.org_id, cases.c.id == identifier)
                .values(
                    status="COMPLETED",
                    active=False,
                    version=cases.c.version + 1,
                    updated_at=sa.func.now(),
                )
            )
            evidence = {
                name: proof[name]
                for name in (
                    "kind",
                    "instance_id",
                    "revision_id",
                    "completed_at",
                    "source_event_ids",
                    "proof_sha256",
                )
            }
            await self.event(
                conn,
                ctx,
                identifier,
                revision_id,
                "CARRIER_HANDOFF_VERIFIED",
                "handoff:" + proof["proof_sha256"],
                evidence,
            )
            return await self.get(ctx, identifier, conn=conn)

    @staticmethod
    async def event(conn, ctx, case_id, revision_id, event_type, key, payload):
        await conn.execute(
            events.insert().values(
                id=uuid4(),
                org_id=ctx.org_id,
                case_id=case_id,
                revision_id=revision_id,
                actor_id=ctx.owner_id,
                event_type=event_type,
                event_key=key,
                payload_hash=fingerprint(payload),
                payload=payload,
            )
        )
