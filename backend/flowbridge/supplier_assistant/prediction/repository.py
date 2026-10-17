# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa

from flowbridge.shared.errors import CoreError, ErrorCode, ErrorDetail
from flowbridge.supplier_assistant.repository import SupplierRepository, table

from .features import OrderFact


class PredictionRepository:
    # Reuses the application-owned SQL adapter; custom nodes never access SQL.
    def __init__(self, repository: SupplierRepository, *, max_rows: int = 20000) -> None:
        self.core = repository
        self.max_rows = max_rows

    async def bounded(self, name: str, *conditions: object) -> list[dict]:
        rows = await self.core.rows(name, *conditions, limit=self.max_rows + 1)
        if len(rows) > self.max_rows:
            raise CoreError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                details=(ErrorDetail(field="history", reason="ROW_LIMIT_EXCEEDED"),),
            )
        return rows

    async def facts(
        self,
        product: dict,
        supplier_ids: list[UUID],
        asof: datetime,
        history_days: int,
    ) -> list[OrderFact]:
        items, orders = table("purchase_order_items"), table("purchase_orders")
        selected_orders = sa.select(orders.c.purchase_order_id).where(
            orders.c.order_date <= asof,
            sa.or_(
                orders.c.order_date >= asof - timedelta(days=history_days),
                sa.and_(
                    orders.c.supplier_id.in_(supplier_ids),
                    orders.c.status.not_in(["delivered", "cancelled", "rejected"]),
                ),
            ),
        )
        rows = await self.bounded(
            "purchase_order_items",
            items.c.purchase_order_id.in_(selected_orders),
            sa.or_(
                items.c.product_id == product["product_id"],
                items.c.product_category_snapshot == product["product_category"],
                items.c.purchase_order_id.in_(
                    sa.select(orders.c.purchase_order_id).where(
                        orders.c.supplier_id.in_(supplier_ids)
                    )
                ),
            ),
        )
        return await self.expand(rows, asof)

    async def order_facts(self, order_id: UUID, asof: datetime) -> list[OrderFact]:
        rows = await self.bounded(
            "purchase_order_items",
            table("purchase_order_items").c.purchase_order_id == order_id,
        )
        return await self.expand(rows, asof)

    async def expand(self, rows: list[dict], asof: datetime) -> list[OrderFact]:
        if not rows:
            return []
        order_ids = {r["purchase_order_id"] for r in rows}
        orders = {
            r["purchase_order_id"]: r
            for r in await self.bounded(
                "purchase_orders",
                table("purchase_orders").c.purchase_order_id.in_(order_ids),
            )
        }
        schedules = await self.bounded(
            "purchase_order_schedules",
            table("purchase_order_schedules").c.po_item_id.in_([r["po_item_id"] for r in rows]),
            table("purchase_order_schedules").c.created_at <= asof,
        )
        schedule_ids = [s["schedule_id"] for s in schedules]
        receipts = (
            await self.bounded(
                "goods_receipts",
                table("goods_receipts").c.schedule_id.in_(schedule_ids),
                table("goods_receipts").c.received_at <= asof,
            )
            if schedule_ids
            else []
        )
        confirmations = await self.bounded(
            "supplier_confirmations",
            table("supplier_confirmations").c.purchase_order_id.in_(order_ids),
            table("supplier_confirmations").c.responded_at <= asof,
        )
        promises = (
            await self.bounded(
                "supplier_confirmation_items",
                table("supplier_confirmation_items").c.schedule_id.in_(schedule_ids),
                table("supplier_confirmation_items").c.recorded_at <= asof,
            )
            if schedule_ids
            else []
        )
        result = []
        for row in rows:
            item_schedules = [s for s in schedules if s["po_item_id"] == row["po_item_id"]]
            ids = {s["schedule_id"] for s in item_schedules}
            result.append(
                OrderFact(
                    item=row,
                    order=orders[row["purchase_order_id"]],
                    schedules=item_schedules,
                    receipts=[r for r in receipts if r["schedule_id"] in ids],
                    confirmations=[
                        c
                        for c in confirmations
                        if c["purchase_order_id"] == row["purchase_order_id"]
                    ],
                    promises=[p for p in promises if p["schedule_id"] in ids],
                )
            )
        return result
