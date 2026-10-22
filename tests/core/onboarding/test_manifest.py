import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.core.applications.manifest import (
    ManifestError,
    manifest_schema,
    parse_manifest,
    source_name,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "contracts/onboarding/examples"


def warehouse():
    return json.loads((EXAMPLES / "warehouse.json").read_text())


@pytest.mark.parametrize("name", ["warehouse.json", "sales-cross-source.json"])
def test_examples_and_exported_schema(name):
    value = json.loads((EXAMPLES / name).read_text())
    schema = manifest_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    assert parse_manifest(json.dumps(value)).manifest_version == "1.0"


@pytest.mark.parametrize(
    "content", ["{", "[]", '{"manifest_version":"1.0","manifest_version":"2.0"}']
)
def test_invalid_json_and_duplicate_keys(content):
    with pytest.raises(ManifestError):
        parse_manifest(content)


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(manifest_version="2.0"),
        lambda value: value["application"].update(owner_id="caller-supplied"),
        lambda value: value["sources"][0].update(password="SYNTHETIC_NEVER_ECHO"),
        lambda value: value["sources"][0].update(secret_ref="postgresql://user:password@host/db"),
        lambda value: value["sources"][0].update(host="http://untrusted.invalid/path"),
        lambda value: value["sources"].append(deepcopy(value["sources"][0])),
        lambda value: value["data_products"].append(deepcopy(value["data_products"][0])),
        lambda value: value["data_products"][0].update(source_key="missing"),
        lambda value: value["data_products"][0]["fields"].append({"column": "sku"}),
        lambda value: value["data_products"][1]["relationships"][0].update(
            target_product="missing"
        ),
        lambda value: value["data_products"][1]["relationships"][0].update(name="sku"),
        lambda value: value["data_products"][1]["relationships"][0].update(
            sql="select * from secrets"
        ),
    ],
)
def test_reject_invalid_manifest(change):
    value = warehouse()
    change(value)
    with pytest.raises(ManifestError) as caught:
        parse_manifest(json.dumps(value))
    assert "SYNTHETIC_NEVER_ECHO" not in str(caught.value)


def test_invalid_example_and_source_names_are_deterministic():
    with pytest.raises(ManifestError):
        parse_manifest((EXAMPLES / "invalid-credentials.json").read_text())
    assert source_name("warehouse", "warehouse-pg") == source_name("warehouse", "warehouse-pg")
    assert source_name("a-b", "c") != source_name("a_b", "c")


def test_committed_schema_matches_model():
    committed = json.loads((EXAMPLES.parent / "application-manifest-v1.schema.json").read_text())
    assert committed == manifest_schema()
