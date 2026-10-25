# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional case storage; no Core/business repositories or source credentials."""

import hashlib
import json
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import IntegrityError

from .decision_models import cases, events, participants, revisions


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
