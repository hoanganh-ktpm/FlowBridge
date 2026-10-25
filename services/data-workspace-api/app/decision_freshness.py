# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable, scoped fingerprints shared by simulation and decision revalidation."""

import hashlib
import json


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
        ).encode()
    ).hexdigest()


def ordered(rows):
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))


def input_digest(warehouse_id, sku, stocks, inbounds, orders, commitments, preparation, forecast):
    def scoped(rows):
        return (
            None
            if rows is None
            else ordered([row for row in rows if str(row.get("warehouse_id")) == str(warehouse_id)])
        )

    return digest(
        {
            "version": "warehouse_operational_v2",
            "warehouse_id": str(warehouse_id),
            "sku": sku,
            "stock": scoped(stocks),
            "inbound": scoped(inbounds),
            "orders": scoped(orders),
            "commitments": scoped(commitments),
            "preparation": scoped(preparation),
            "forecast": ordered([row for row in forecast if row["item_id"] == sku]),
        }
    )
