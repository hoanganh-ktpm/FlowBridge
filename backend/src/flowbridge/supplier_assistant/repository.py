# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# SQL boundary: named tables from registered metadata, bound values and a request-owned session.
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from flowbridge.models import Base
from flowbridge.shared.errors import CoreError, ErrorCode


def table(name: str) -> sa.Table:
    return Base.metadata.tables[name]


def json_value(value: Any, *, scale: int | None = None) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value:.4f}" if scale == 4 else float(value)
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_value(item) for item in value]
    return value


def dto(name: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        column.name: json_value(row[column.name], scale=getattr(column.type, "scale", None))
        for column in table(name).c
    }


def values_for(name: str, values: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in values.items():
        column = table(name).c[key]
        if value is not None:
            if isinstance(column.type, sa.Uuid):
                value = UUID(str(value))
            elif isinstance(column.type, sa.DateTime) and isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            elif isinstance(column.type, sa.Date) and isinstance(value, str):
                value = date.fromisoformat(value)
            elif isinstance(column.type, sa.Numeric):
                value = Decimal(str(value))
        result[key] = value
    return result


class SupplierRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def rows(
        self,
        name: str,
        *conditions: Any,
        lock: bool = False,
        limit: int | None = None,
        offset: int = 0,
        order_by: Any = None,
    ) -> list[dict[str, Any]]:
        target = table(name)
        statement = (
            sa.select(target)
            .where(*conditions)
            .order_by(order_by if order_by is not None else list(target.primary_key.columns)[0])
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        if lock:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return [dict(row) for row in result.mappings()]

    async def one(self, name: str, *conditions: Any, lock: bool = False) -> dict[str, Any]:
        rows = await self.rows(name, *conditions, lock=lock, limit=1)
        if not rows:
            raise CoreError(ErrorCode.NOT_FOUND)
        return rows[0]

    async def get(self, name: str, identifier: str | UUID, *, lock: bool = False) -> dict[str, Any]:
        pk = list(table(name).primary_key.columns)[0]
        return await self.one(name, pk == UUID(str(identifier)), lock=lock)

    async def insert(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        result = await self.session.execute(
            sa.insert(table(name)).values(**values_for(name, values)).returning(table(name))
        )
        return dict(result.mappings().one())

    async def update(self, name: str, identifier: UUID, values: dict[str, Any]) -> dict[str, Any]:
        target = table(name)
        pk = list(target.primary_key.columns)[0]
        result = await self.session.execute(
            sa.update(target)
            .where(pk == identifier)
            .values(**values_for(name, values))
            .returning(target)
        )
        return dict(result.mappings().one())

    async def count(self, name: str, *conditions: Any) -> int:
        return int(
            (
                await self.session.execute(
                    sa.select(sa.func.count()).select_from(table(name)).where(*conditions)
                )
            ).scalar_one()
        )

    async def aggregate(self, name: str, row: dict[str, Any]) -> dict[str, Any]:
        return (await self.aggregates(name, [row]))[0]

    async def aggregates(self, name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = [dto(name, row) for row in rows]
        if not rows:
            return result
        relations = {
            "purchase_requests": ("request_id", "purchase_request_items", "request_id"),
            "proposals": ("proposal_id", "proposal_items", "proposal_id"),
            "purchase_orders": ("purchase_order_id", "purchase_order_items", "purchase_order_id"),
            "supplier_confirmations": (
                "confirmation_id",
                "supplier_confirmation_items",
                "confirmation_id",
            ),
        }
        if name in relations:
            pk, child, fk = relations[name]
            child_rows = await self.rows(child, table(child).c[fk].in_([r[pk] for r in rows]))
            groups: dict[str, list[dict[str, Any]]] = {str(r[pk]): [] for r in rows}
            for item in child_rows:
                groups[str(item[fk])].append(dto(child, item))
            for row in result:
                row["items"] = sorted(
                    groups[row[pk]],
                    key=lambda r: (
                        r.get("line_number", 0),
                        r.get("revision_no", 0),
                        str(next(iter(r.values()))),
                    ),
                )
            if name == "purchase_orders":
                schedule_rows = await self.rows(
                    "purchase_order_schedules",
                    table("purchase_order_schedules").c.po_item_id.in_(
                        [r["po_item_id"] for r in child_rows]
                    ),
                )
                for row in result:
                    ids = {item["po_item_id"] for item in row["items"]}
                    row["schedules"] = [
                        dto("purchase_order_schedules", item)
                        for item in schedule_rows
                        if str(item["po_item_id"]) in ids
                    ]
        return result

    async def page(
        self,
        name: str,
        conditions: list[Any],
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        rows = await self.rows(name, *conditions, limit=page_size, offset=(page - 1) * page_size)
        return {
            "items": await self.aggregates(name, rows),
            "page": page,
            "page_size": page_size,
            "total": await self.count(name, *conditions),
        }
