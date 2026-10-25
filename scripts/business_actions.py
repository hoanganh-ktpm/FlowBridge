# SPDX-License-Identifier: GPL-3.0-or-later
"""Export/review native owner capabilities; registration is an explicit admin command."""

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "contracts/business-operations.actions.json"


def module(domain):
    name = "native_action_contract_" + domain
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "services" / (domain + "-api") / "app/operations.py"
        )
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[name] = loaded
        spec.loader.exec_module(loaded)
    return sys.modules[name]


def definitions():
    from app.platform.business_operations import (
        CommandReceiptQuery,
        EtaCommand,
        PreparationCommand,
        WarehouseScope,
    )

    sales, warehouse, logistics = (module(domain) for domain in ("sales", "warehouse", "logistics"))
    specifications = [
        ("sales", "command_receipt_read", "commands/read", CommandReceiptQuery),
        ("warehouse", "command_receipt_read", "commands/read", CommandReceiptQuery),
        ("logistics", "command_receipt_read", "commands/read", CommandReceiptQuery),
        ("sales", "order_read", "orders/read", sales.ReadOrder),
        ("sales", "reconcile_handoff", "orders/reconcile", sales.ReconcileOrder),
        ("warehouse", "inventory_read", "inventory/read", WarehouseScope),
        ("warehouse", "inbound_read", "inbound/read", warehouse.ReadInbound),
        ("warehouse", "reservation_read", "reservations/read", warehouse.ReadOrder),
        ("warehouse", "dispatch_read", "dispatches/read", warehouse.ReadDispatch),
        ("warehouse", "handoff_proof_read", "handoff-proof/read", warehouse.ReadOrder),
        ("warehouse", "confirm_preparation", "preparation", PreparationCommand),
        ("warehouse", "update_inbound_eta", "inbound/eta", EtaCommand),
        ("warehouse", "post_receipt", "receipts", warehouse.ReceiveCommand),
        ("warehouse", "reserve_order", "reservations", warehouse.ReserveCommand),
        ("warehouse", "prepare_dispatch", "dispatches/prepare", warehouse.PrepareCommand),
        ("warehouse", "record_carrier_handoff", "dispatches/handoff", warehouse.HandoffCommand),
        ("logistics", "capacity_read", "capacity/read", WarehouseScope),
        ("logistics", "shipment_read", "shipments/read", logistics.ReadShipment),
        ("logistics", "book_carrier", "shipments", logistics.BookCommand),
        ("logistics", "reconcile_handoff", "shipments/reconcile", logistics.ReconcileCommand),
    ]
    actions = []
    for domain, name, path, body in specifications:
        output = {"type": "object"}
        if name == "handoff_proof_read":
            output = {
                "type": "object",
                "required": [
                    "org_id",
                    "warehouse_id",
                    "order_id",
                    "source_order_revision",
                    "complete",
                    "completed_at",
                    "lines",
                    "evidence",
                ],
                "properties": {
                    "org_id": {"type": "string", "format": "uuid"},
                    "warehouse_id": {"type": "string", "format": "uuid"},
                    "order_id": {"type": "string", "format": "uuid"},
                    "source_order_revision": {"type": "integer", "minimum": 1},
                    "complete": {"type": "boolean"},
                    "completed_at": {"type": ["string", "null"]},
                    "lines": {"type": "array", "maxItems": 20},
                    "evidence": {"type": "array", "maxItems": 100},
                },
            }
        actions.append(
            {
                "action_id": domain + ".operations." + name,
                "version": 1,
                "owner_application": domain,
                "implementation_type": "HTTP",
                "input_schema": body.model_json_schema(),
                "output_schema": output,
                "service_ref": domain,
                "method": "POST",
                "path": "/api/v1/" + domain + "/operations/" + path,
                "timeout_seconds": 30,
            }
        )
    return {
        "schema_version": "1.0",
        "actions": actions,
        "manual_confirmation_required": ["warehouse.operations.record_carrier_handoff"],
        "notes": [
            "Action registration does not grant native owner permissions "
            "or approve a decision case.",
            "Actor bindings require org and warehouse scope at each owner API. "
            "Human MFA is unchanged.",
            "Physical handoff rejects machine identities and requires an actual receiver, "
            "receipt number and nonfuture time.",
        ],
    }


def register(core_url, token):
    parsed = urlsplit(core_url)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or (
            parsed.scheme != "https"
            and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"})
        )
    ):
        raise ValueError("Use a trusted Core HTTPS or loopback origin")
    if not token or "\n" in token or "\r" in token:
        raise ValueError("Set FLOWBRIDGE_ACTION_REGISTRATION_TOKEN in the process environment")
    with httpx.Client(timeout=40, follow_redirects=False) as http:
        for definition in definitions()["actions"]:
            key = hashlib.sha256(
                (definition["action_id"] + ":" + str(definition["version"])).encode()
            ).hexdigest()
            response = http.post(
                core_url.rstrip("/") + "/api/v1/workflows/actions",
                json=definition,
                headers={
                    "Authorization": "Bearer " + token,
                    "Idempotency-Key": "native-action:" + key,
                },
            )
            if response.status_code != 201:
                raise RuntimeError(
                    f"Action registration stopped at {definition['action_id']} "
                    f"(HTTP {response.status_code}); no owner data command was executed"
                )
            print("Registered " + definition["action_id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["export", "register"])
    parser.add_argument("--core-url", default="http://127.0.0.1:18000")
    args = parser.parse_args()
    if args.operation == "export":
        DESTINATION.write_text(
            json.dumps(definitions(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("Exported 20 native owner action definitions; no registration or business writes")
    else:
        register(args.core_url, os.environ.get("FLOWBRIDGE_ACTION_REGISTRATION_TOKEN", ""))


if __name__ == "__main__":
    main()
