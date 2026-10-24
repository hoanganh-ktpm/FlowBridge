# SPDX-License-Identifier: GPL-3.0-or-later
"""Only Layer 2-owned configuration and bounded results; no business history mirror."""

import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

TABLES = {
    "config": "analysis_configs",
    "forecast": "forecast_runs",
    "simulation": "simulation_runs",
}


def validate_url(value):
    url = make_url(value)
    if (
        url.database != "flowbridge_workspace_db"
        or url.username != "workspace_owner"
        or url.drivername not in {"postgresql", "postgresql+psycopg"}
        or url.query
    ):
        raise ValueError(
            "Workspace requires workspace_owner@flowbridge_workspace_db; no URL overrides"
        )
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


class PostgresRepository:
    def __init__(self, url):
        self.url = validate_url(url)
        self.engine = create_async_engine(
            make_url(self.url).set(drivername="postgresql+asyncpg"),
            hide_parameters=True,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=2,
        )

    async def close(self):
        await self.engine.dispose()

    async def save(self, kind, ctx, payload):
        identifier = str(uuid4())
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    f"INSERT INTO data_workspace.{TABLES[kind]} (id,org_id,owner_id,payload) "
                    "VALUES (:id,:org,:owner,CAST(:payload AS jsonb))"
                ),
                {
                    "id": identifier,
                    "org": ctx.org_id,
                    "owner": ctx.owner_id,
                    "payload": json.dumps(payload, allow_nan=False),
                },
            )
        return {"id": identifier, **payload}

    async def get(self, kind, identifier, ctx):
        async with self.engine.connect() as conn:
            result = await conn.execute(
                text(
                    f"SELECT id,payload FROM data_workspace.{TABLES[kind]} "
                    "WHERE id=:id AND org_id=:org AND owner_id=:owner"
                ),
                {"id": identifier, "org": ctx.org_id, "owner": ctx.owner_id},
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "Result not found")
        return {"id": str(row["id"]), **row["payload"]}

    async def list(self, kind, ctx):
        async with self.engine.connect() as conn:
            result = await conn.execute(
                text(
                    f"SELECT id,payload,created_at FROM data_workspace.{TABLES[kind]} "
                    "WHERE org_id=:org AND owner_id=:owner ORDER BY created_at DESC,id LIMIT 100"
                ),
                {"org": ctx.org_id, "owner": ctx.owner_id},
            )
            rows = result.mappings().all()
        return [{"id": str(row["id"]), **row["payload"]} for row in rows]
