# SPDX-License-Identifier: GPL-3.0-or-later
"""Authorized warehouse/product directory, without quantities or forecasting."""

import asyncio
from collections import defaultdict

from fastapi import HTTPException


async def planning_options(body, ctx, connector, source_names):
    async def read(role, columns):
        ref = getattr(body, role)
        if ref is None:
            return [], None
        product, rows = await connector.rows(ctx, ref, columns)
        if product["name"] != source_names[role]:
            raise HTTPException(422, "Directory source does not match its semantic role")
        return rows, {"ref": ref.model_dump(mode="json"), "columns": columns}

    (stocks, stock_source), (catalog, catalog_source) = await asyncio.gather(
        read("stock", ["warehouse_id", "sku", "uom"]),
        read(
            "catalog",
            ["warehouse_id", "sku", "product_name", "uom", "warehouse_code", "warehouse_name"],
        ),
    )
    warehouse_ids = sorted({str(r["warehouse_id"]) for r in stocks})
    warehouse_labels, product_labels = defaultdict(list), defaultdict(list)
    for row in catalog:
        warehouse_labels[str(row["warehouse_id"])].append(row)
        product_labels[(str(row["warehouse_id"]), row["sku"], row["uom"])].append(row)

    def unique(rows, key):
        values = {str(r[key]) for r in rows if r.get(key)}
        return next(iter(values)) if len(values) == 1 else None

    warehouses = []
    for identifier in warehouse_ids:
        labels = warehouse_labels[identifier]
        warehouses.append(
            {
                "id": identifier,
                "name": unique(labels, "warehouse_name"),
                "code": unique(labels, "warehouse_code"),
            }
        )
    products = []
    seen = {}
    for row in sorted(stocks, key=lambda r: (str(r["warehouse_id"]), r["sku"])):
        key = (str(row["warehouse_id"]), row["sku"])
        if key in seen:
            if seen[key] != row["uom"]:
                raise HTTPException(422, "Ambiguous warehouse/product unit")
            continue
        seen[key] = row["uom"]
        labels = product_labels[(*key, row["uom"])]
        products.append(
            {
                "warehouse_id": key[0],
                "sku": key[1],
                "uom": row["uom"],
                "name": unique(labels, "product_name"),
            }
        )
    return {"warehouses": warehouses, "products": products}, [
        s for s in [stock_source, catalog_source] if s is not None
    ]
