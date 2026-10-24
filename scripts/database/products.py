"""Draft-only mappings for the six domain-owned Data Products; no publish/grant calls."""

import json

import sqlalchemy as sa

from app.core.data_sources.discovery import canonical_type
from scripts.database.config import database_url
from scripts.database.engine import make_engine
from scripts.database.local import RUNTIME

PRODUCTS = {
    "sales": {
        "confirmed_demand_daily": ("org_id", "sku", "day"),
        "open_orders": ("org_id", "order_line_id"),
    },
    "warehouse": {
        "stock_availability": ("org_id", "warehouse_id", "product_id"),
        "inbound_schedule": ("org_id", "inbound_plan_id"),
    },
    "logistics": {
        "handoff_capacity": ("org_id", "carrier_code", "capacity_date"),
        "accepted_shipments_daily": ("org_id", "carrier_code", "day"),
    },
}


def view_keys():
    return {
        f"{domain}-reader": {f"public.v_{key}": grain for key, grain in products.items()}
        for domain, products in PRODUCTS.items()
    }


def draft_submissions():
    submissions = {}
    for domain, products in PRODUCTS.items():
        url = database_url(domain)
        engine = make_engine(domain)
        try:
            inspector = sa.inspect(engine)
            manifest_products, contracts = [], []
            for key, grain in products.items():
                fields = []
                for column in inspector.get_columns(f"v_{key}", schema="public"):
                    pg_type = column["type"].compile(dialect=engine.dialect)
                    wire = {
                        "integer": "integer",
                        "bigint": "integer",
                        "numeric": "number",
                        "date": "date",
                        "timestamptz": "date-time",
                    }.get(canonical_type(pg_type), "string")
                    fields.append(
                        {
                            "column": column["name"],
                            "name": column["name"],
                            "physical_type": pg_type,
                            "wire_type": wire,
                            "nullable": False if column["name"] in grain else column["nullable"],
                            "description": None,
                        }
                    )
                manifest_products.append(
                    {
                        "key": key,
                        "name": f"{domain}.{key}",
                        "source_key": "pg",
                        "object": {"schema": "public", "name": f"v_{key}", "kind": "view"},
                        "fields": [
                            {
                                "column": f["column"],
                                "expected_type": canonical_type(f["physical_type"]),
                            }
                            for f in fields
                        ],
                        "contract_version": "1.0.0",
                        "requested_state": "DRAFT",
                    }
                )
                contracts.append(
                    {
                        "product_key": key,
                        "version": "1.0.0",
                        "contract_json": {
                            "document_version": "1.0",
                            "grain": "One row per " + ", ".join(grain),
                            "business_key": list(grain),
                            "classification": "INTERNAL",
                            "description": None,
                            "fields": fields,
                            "relationships": [],
                        },
                    }
                )
            submissions[domain] = {
                "manifest": {
                    "manifest_version": "1.0",
                    "application": {
                        "key": domain,
                        "name": f"Local {domain}",
                        "department_key": domain,
                    },
                    "sources": [
                        {
                            "key": "pg",
                            "kind": "postgres",
                            "host": url.host,
                            "port": url.port,
                            "database": url.database,
                            "username": f"{domain}_reader",
                            "ssl_mode": "require",
                            "secret_ref": f"{domain}-reader",
                        }
                    ],
                    "data_products": manifest_products,
                },
                "product_contracts": contracts,
            }
        finally:
            engine.dispose()
    return submissions


def export_drafts():
    target = RUNTIME / "draft-products.json"
    target.write_text(json.dumps(draft_submissions(), indent=2), encoding="utf-8")
    print("Six DRAFT product mappings exported to ignored .tmp/local-postgres/draft-products.json")
