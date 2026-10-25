# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from scripts.business_actions import definitions, module

from app.platform.workflow.actions import ActionDefinition

ROOT = Path(__file__).resolve().parents[2]


def test_native_action_contracts_match_code_and_have_no_url_or_sql_inputs():
    contract = definitions()
    assert (
        json.loads(
            (ROOT / "contracts/business-operations.actions.json").read_text(encoding="utf-8")
        )
        == contract
    )
    assert len(contract["actions"]) == 20
    assert len({a["action_id"] for a in contract["actions"]}) == 20
    for entry in contract["actions"]:
        definition = ActionDefinition(**entry)
        assert definition.implementation_type == "HTTP" and definition.method == "POST"
        assert definition.owner_application == definition.service_ref
        assert definition.path.startswith(
            "/api/v1/" + definition.owner_application + "/operations/"
        )
        Draft202012Validator.check_schema(entry["input_schema"])
        Draft202012Validator.check_schema(entry["output_schema"])
        properties = entry["input_schema"]["properties"]
        assert {"org_id", "warehouse_id"} <= set(properties)
        assert not {"sql", "url", "status", "role", "approved", "service_base"}.intersection(
            properties
        )
        assert entry["input_schema"]["additionalProperties"] is False


def test_owner_command_models_reject_ambiguous_time_and_fabricated_status():
    from uuid import uuid4

    warehouse = module("warehouse")
    body = dict(
        org_id=uuid4(),
        warehouse_id=uuid4(),
        dispatch_id=uuid4(),
        shipment_id=uuid4(),
        expected_dispatch_version=1,
        expected_manifest_hash="a" * 64,
        carrier_receiver_name="Lê Văn Bình",
        carrier_receipt_number="BG-01",
        handed_over_at="2026-10-25T09:00:00+07:00",
    )
    for changes in (
        {"carrier_receipt_number": " "},
        {"handed_over_at": "2026-10-25T09:00:00"},
        {"status": "HANDED_OVER"},
        {"expected_dispatch_version": True},
    ):
        with pytest.raises(ValidationError):
            warehouse.HandoffCommand.model_validate({**body, **changes})
