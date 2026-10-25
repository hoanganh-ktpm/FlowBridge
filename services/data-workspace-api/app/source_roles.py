# SPDX-License-Identifier: GPL-3.0-or-later
"""Deployment-owned semantic source bindings; never grants or database connections."""

import json
import os

DEFAULT_SOURCE_NAMES = {
    "demand": "sales.planning_demand_daily",
    "stock": "warehouse.stock_availability",
    "inbound": "warehouse.inbound_schedule",
    "open_orders": "sales.open_orders",
    "commitments": "warehouse.order_commitments",
    "catalog": "warehouse.planning_catalog",
    "receipts": "warehouse.receipts_daily",
    "schedule": "logistics.handoff_schedule",
    "capacity": "logistics.handoff_capacity",
    "sales_history": "sales.confirmed_demand_daily",
    "transport_history": "logistics.accepted_shipments_daily",
}


def configured_source_names():
    overrides = json.loads(os.environ.get("FLOWBRIDGE_WORKSPACE_SOURCE_NAMES", "{}"))
    if not isinstance(overrides, dict) or set(overrides) - DEFAULT_SOURCE_NAMES.keys():
        raise ValueError("Unknown Workspace semantic source role")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 160 for v in overrides.values()):
        raise ValueError("Workspace source names must be nonempty strings up to 160 characters")
    names = {**DEFAULT_SOURCE_NAMES, **overrides}
    if len(set(names.values())) != len(names):
        raise ValueError("Workspace semantic roles require distinct source names")
    return names
