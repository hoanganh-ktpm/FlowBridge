# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only real HTTP acceptance and timings, using the demo executor identity."""

import json
from datetime import datetime
from time import perf_counter

import httpx

from scripts.decision_demo_access import service_token
from scripts.decision_demo_data import DIRECTORY


def probe():
    data = json.loads((DIRECTORY / "data-resources.json").read_text(encoding="utf-8"))
    access = json.loads((DIRECTORY / "access-resources.json").read_text())
    if not access.get("configured"):
        raise ValueError("Finish actual Core access setup first")
    roles = {
        "demand": "sales.planning_demand_daily",
        "stock": "warehouse.stock_availability",
        "inbound": "warehouse.inbound_schedule",
        "open_orders": "sales.open_orders",
        "commitments": "warehouse.order_commitments",
        "catalog": "warehouse.planning_catalog",
        "receipts": "warehouse.receipts_daily",
        "schedule": "logistics.handoff_schedule",
        "capacity": "logistics.handoff_capacity",
        "preparation": "warehouse.handoff_preparation_policy",
    }
    products = {
        value["name"]: {"data_product_id": key, "contract_version_id": value["contract_version_id"]}
        for key, value in access["grants"].items()
    }
    references = {key: products[name] for key, name in roles.items()}
    timings = []
    with httpx.Client(
        base_url="http://127.0.0.1:18090/api/v1/data-workspace",
        timeout=120,
        headers={"Authorization": "Bearer " + service_token("executor")},
        follow_redirects=False,
    ) as http:

        def request(method, path, body=None):
            started = perf_counter()
            response = http.request(method, path, json=body)
            elapsed = perf_counter() - started
            timings.append(
                {"path": path, "status": response.status_code, "seconds": round(elapsed, 3)}
            )
            if response.status_code != 200:
                # Only bounded business error detail; never request headers/secrets.
                detail = response.json().get("detail", "unavailable")
                raise RuntimeError(f"Demo {path} HTTP {response.status_code}: {str(detail)[:300]}")
            return response.json()

        options = request(
            "POST",
            "/planning-options",
            {"stock": references["stock"], "catalog": references["catalog"]},
        )
        assert data["warehouse_id"] in {w["id"] for w in options["warehouses"]}
        planning = request(
            "POST",
            "/planning",
            {
                **references,
                "warehouse_id": data["warehouse_id"],
                "sku": "KB-K120",
                "horizon": 7,
                "training_days": 180,
                "forecast_model": "ridge_lag",
            },
        )
        assert planning["selected_sku"] == "KB-K120"
        assert len(planning["products"]) == 8
        assert planning["forecast"]["model"]["entities"]["KB-K120"]["method"] == "ridge_lag"
        by_id = {row["order_id"]: row for row in planning["orders"]}
        assert all(o["id"] in by_id for o in data["orders"])
        assert len(by_id[data["orders"][1]["id"]]["lines"]) == 2
        scenario = request(
            "POST",
            "/scenarios",
            {
                **{
                    k: references[k]
                    for k in ("stock", "inbound", "open_orders", "commitments", "preparation")
                },
                "forecast_run_id": planning["forecast"]["id"],
                "warehouse_id": data["warehouse_id"],
                "sku": "KB-K120",
                "arrival_date": data["day"],
                "additional_quantity": 500,
                "require_ml": True,
                "include_sensitivity": False,
                "save": False,
            },
        )
        assert scenario["evidence"]["metric_semantics_version"] == "physical_supply_deficit_v1"
        assert (
            scenario["decision"]["stressed"]["peak_shortage"]
            >= scenario["decision"]["proposed"]["peak_shortage"]
        )
        assert {o["order_number"] for o in scenario["baseline"]["orders"]} >= {
            o["number"] for o in data["orders"]
        }
        # Actual machine identity can read planning but has no human task assignment.
        capabilities = request("GET", "/decision-capabilities")
        assert capabilities["enabled"] is False
    result = {
        "profile_day": data["day"],
        "org_id": data["org_id"],
        "checked_at": datetime.now().astimezone().isoformat(),
        "scope": (
            "actual service OAuth, Core, Hasura, PostgreSQL, Workspace HTTP; "
            "no four-human MFA claim"
        ),
        "timings": timings,
        "planning_sources": [
            {
                "name": source["name"],
                "rows": source["rows"],
                "seconds": round(
                    (
                        datetime.fromisoformat(source["read_completed_at"])
                        - datetime.fromisoformat(source["read_started_at"])
                    ).total_seconds(),
                    3,
                ),
            }
            for source in planning["evidence"]["sources"]
        ],
        "history_days": 240,
        "products": len(planning["products"]),
        "forecast_method": "ridge_lag",
        "acceptance_orders": [by_id[o["id"]]["order_number"] for o in data["orders"]],
        "before_shortage": scenario["decision"]["stressed"]["peak_shortage"],
        "after_shortage": scenario["decision"]["proposed"]["peak_shortage"],
        "human_acceptance": "NOT_RUN",
    }
    (DIRECTORY / "live-probe.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    probe()
