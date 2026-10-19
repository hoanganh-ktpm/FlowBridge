# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import hashlib
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from flowbridge.agent_hub.contract import HubError, validate
from flowbridge.agent_hub.projection import result_url
from flowbridge.models import agent_hub as db


class HubRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def rows(
        self,
        table: sa.Table,
        *where: Any,
        order: Any = None,
        limit: int | None = None,
        lock: bool = False,
    ) -> list[dict]:
        query = sa.select(table).where(*where)
        if order is not None:
            query = query.order_by(order)
        if limit is not None:
            query = query.limit(limit)
        if lock:
            query = query.with_for_update()
        return [dict(row) for row in (await self.session.execute(query)).mappings()]

    async def one(self, table: sa.Table, *where: Any, lock: bool = False) -> dict:
        rows = await self.rows(table, *where, limit=1, lock=lock)
        if not rows:
            raise HubError(404, "NOT_FOUND", "Không tìm thấy tài nguyên.")
        return rows[0]

    async def insert(self, table: sa.Table, values: dict) -> dict:
        result = await self.session.execute(sa.insert(table).values(**values).returning(table))
        return dict(result.mappings().one())

    async def update(self, table: sa.Table, identifier: str, values: dict) -> dict:
        result = await self.session.execute(
            sa.update(table).where(table.c.id == identifier).values(**values).returning(table)
        )
        return dict(result.mappings().one())

    async def advisory_lock(self, scope: str) -> None:
        key = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], "big", signed=True)
        acquired = (
            await self.session.execute(sa.select(sa.func.pg_try_advisory_xact_lock(key)))
        ).scalar_one()
        if not acquired:
            raise HubError(
                409, "IDEMPOTENCY_IN_PROGRESS", "Yêu cầu cùng khóa đang được tiếp nhận.", True
            )

    async def event(
        self, run: dict, kind: str, data: dict | None = None, occurred_at: str | None = None
    ) -> dict:
        sequence = run["next_event"]
        event = {
            "schema_version": "1.0",
            "id": f"{run['id']}:{sequence}",
            "sequence": sequence,
            "type": kind,
            "run_id": run["id"],
            "occurred_at": occurred_at or datetime.now(UTC).isoformat(),
            "data": data or {"status": run["status"], "result_url": result_url(run["id"])},
        }
        validate(event, "RunEvent")
        await self.insert(db.events, {"run_id": run["id"], "sequence": sequence, "body": event})
        run["next_event"] += 1
        await self.update(db.runs, run["id"], {"next_event": run["next_event"]})
        return event

    async def encrypt(self, token: str, key: str) -> bytes:
        return (
            await self.session.execute(
                sa.text("SELECT extensions.pgp_sym_encrypt(:token, :key, 'cipher-algo=aes256')"),
                {"token": token, "key": key},
            )
        ).scalar_one()

    async def decrypt(self, cipher: bytes, key: str) -> str:
        return (
            await self.session.execute(
                sa.text("SELECT extensions.pgp_sym_decrypt(:cipher, :key)"),
                {"cipher": cipher, "key": key},
            )
        ).scalar_one()
