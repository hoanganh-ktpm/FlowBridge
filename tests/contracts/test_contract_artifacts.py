# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge, licensed under GNU GPL version 3 or later.
# You may redistribute and modify it under that license. It is provided without
# any warranty, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE for the full terms.

# Validate specifications and synthetic fixtures; these are not runtime API tests.

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SPEC_FILES = sorted(CONTRACTS.glob("*.openapi.yaml"))
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in mapping:
            raise ValueError(f"Duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


@lru_cache
def load_document(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    return json.loads(source) if path.suffix == ".json" else yaml.load(source, UniqueKeyLoader)


def reference_target(reference: str, base: Path) -> tuple[Any, Path]:
    filename, _, pointer = reference.partition("#")
    target_path = (base.parent / filename).resolve() if filename else base
    assert target_path.is_relative_to(CONTRACTS), f"Reference escapes contracts: {reference}"
    target: Any = load_document(target_path)
    assert not pointer or pointer.startswith("/"), f"Unsupported pointer: {reference}"
    if pointer:
        for raw_key in pointer[1:].split("/"):
            key = raw_key.replace("~1", "/").replace("~0", "~")
            target = target[int(key)] if isinstance(target, list) else target[key]
    return target, target_path


def expand_references(value: Any, base: Path, stack: tuple[str, ...] = ()) -> Any:
    if isinstance(value, list):
        return [expand_references(item, base, stack) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        identity = str(base) + "#" + value["$ref"]
        assert identity not in stack, "Recursive refs need a registry-aware validator"
        target, target_path = reference_target(value["$ref"], base)
        expanded = expand_references(target, target_path, (*stack, identity))
        siblings = {
            key: expand_references(item, base, stack)
            for key, item in value.items()
            if key != "$ref"
        }
        # Schema $ref siblings are conjunctive; descriptive siblings do not change assertions.
        return {"allOf": [expanded, siblings]} if siblings else expanded
    return {key: expand_references(item, base, stack) for key, item in value.items()}


def operations(path: Path) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (route, method, operation)
        for route, item in load_document(path)["paths"].items()
        for method, operation in item.items()
        if method in HTTP_METHODS
    ]


def validator(schema: dict[str, Any], base: Path) -> Draft202012Validator:
    expanded = expand_references(schema, base)
    Draft202012Validator.check_schema(expanded)
    return Draft202012Validator(expanded, format_checker=FormatChecker())


def test_required_contract_artifacts_exist() -> None:
    assert {path.name for path in SPEC_FILES} == {
        "common.openapi.yaml", "core-api.openapi.yaml",
        "domain-api.openapi.yaml", "platform-api.openapi.yaml",
    }
    assert load_document(CONTRACTS / "fixtures/review-cases.json")["cases"]
    assert load_document(CONTRACTS / "identity-context.schema.json")["title"] == "VerifiedIdentity"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda path: path.name)
def test_specs_references_and_json_schemas(path: Path) -> None:
    document = load_document(path)
    assert document["openapi"] == "3.1.1"
    assert document["x-contract-status"] == "review-candidate"
    assert document["security"] == [{"bearerAuth": []}]
    assert document["info"]["license"]["identifier"] == "GPL-3.0-or-later"
    # Also traverses non-schema refs (parameters, responses, headers).
    expand_references(document, path)
    for schema in document["components"]["schemas"].values():
        validator(schema, path)


def test_operation_ids_are_unique_across_all_specs() -> None:
    ids = [operation["operationId"] for path in SPEC_FILES for _, _, operation in operations(path)]
    assert ids
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda path: path.name)
def test_http_boundaries_and_inline_examples(path: Path) -> None:
    for route, method, operation in operations(path):
        assert route.startswith("/api/v1/")
        assert operation["x-authorization"]
        assert operation["x-priority"] in {"P0", "P1"}
        params = [expand_references(param, path) for param in operation["parameters"]]
        route_names = set(re.findall(r"\{([^}]+)\}", route))
        assert route_names == {param["name"] for param in params if param["in"] == "path"}
        assert all(param["required"] for param in params if param["in"] == "path")
        assert any(param["name"] == "X-Correlation-Id" for param in params)
        if any(param["name"] == "cursor" for param in params):
            assert operation["x-pagination"]["cursor_bound_to_filters"]
            assert operation["x-pagination"]["sort"]
        if method != "get":
            assert any(
                param["name"] == "Idempotency-Key" and param["required"] for param in params
            )
            media = operation["requestBody"]["content"]["application/json"]
            request_validator = validator(media["schema"], path)
            for example in media.get("examples", {}).values():
                request_validator.validate(example["value"])
        else:
            assert "requestBody" not in operation
        for status, response in operation["responses"].items():
            if status.startswith("2"):
                assert "X-Correlation-Id" in response["headers"]
                validator(response["content"]["application/json"]["schema"], path)
                if status == "202":
                    assert {"Location", "Retry-After"} <= response["headers"].keys()


FIXTURE_FILE = CONTRACTS / "fixtures/review-cases.json"
CASES = load_document(FIXTURE_FILE)["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_review_fixture_matches_declared_schema(case: dict[str, Any]) -> None:
    schema, path = reference_target(case["schema"], FIXTURE_FILE)
    errors = list(validator(schema, path).iter_errors(case["payload"]))
    if case["valid"]:
        assert not errors, "\n".join(error.message for error in errors)
    else:
        assert errors, "Negative fixture unexpectedly accepted"


def test_registered_example_input_and_output_use_canonical_operation_schemas() -> None:
    payloads = {case["name"]: case["payload"] for case in CASES}
    operation = payloads["register-stock-lookup-operation"]
    execute = payloads["execute-stock-lookup"]
    result = payloads["stock-lookup-result"]
    validator(operation["input_schema_json"], FIXTURE_FILE).validate(execute["variables"])
    output = validator(operation["output_schema_json"], FIXTURE_FILE)
    output.validate(result["result"])
    assert not output.is_valid({"items": [{"sku": "SP001", "available_quantity": -1}]})
    assert not output.is_valid({"password": "SYNTHETIC_NOT_A_CREDENTIAL"})


def test_example_governance_story_does_not_claim_unapplied_access() -> None:
    payloads = {case["name"]: case["payload"] for case in CASES}
    pending = payloads["grant-pending-not-readable"]
    active = payloads["grant-active"]
    revoking = payloads["grant-revoking"]
    accepted = payloads["approve-202-response"]
    assert pending["status"] == "PENDING_APPLY" and pending["applied_revision"] == 0
    assert active["status"] == "ACTIVE"
    assert active["desired_revision"] == active["applied_revision"]
    assert revoking["desired_revision"] > revoking["applied_revision"]
    assert accepted["access_request"]["status"] == "APPROVED"
    assert accepted["grant"] == pending
    assert payloads["apply-job-queued"]["access_grant_id"] == pending["id"]


def test_read_dtos_exclude_sensitive_provisioning_configuration() -> None:
    schemas = load_document(CONTRACTS / "core-api.openapi.yaml")["components"]["schemas"]
    for name in ["Source", "Operation", "Grant", "AuditEvent"]:
        assert not {
            "password", "access_token", "refresh_token", "secret_ref", "request_config_json",
            "enforcement_role", "query_text", "host", "db_user", "diff_json",
        }.intersection(schemas[name]["properties"])


def test_business_operation_fixture_maps_to_real_domain_contract() -> None:
    payloads = {case["name"]: case["payload"] for case in CASES}
    registered = payloads["register-reserve-stock-operation"]
    config = registered["request_config_json"]
    domain_path = CONTRACTS / "domain-api.openapi.yaml"
    matches = [
        (path, method, op)
        for path, method, op in operations(domain_path)
        if op["operationId"] == config["target_operation_id"]
    ]
    assert len(matches) == 1
    path, method, target = matches[0]
    assert config["path"] == path and config["method"].lower() == method
    variables = payloads["business-api-variables"]
    input_validator = validator(registered["input_schema_json"], FIXTURE_FILE)
    input_validator.validate(variables)
    validator(
        target["requestBody"]["content"]["application/json"]["schema"], domain_path
    ).validate(variables["body"])
    validator(registered["output_schema_json"], FIXTURE_FILE).validate(payloads["reserve-response"])
    assert not input_validator.is_valid({**variables, "path": {"unregistered_id": "fake"}})
    assert not input_validator.is_valid({**variables, "body": {"items": []}})


def test_p0_read_boundary_has_version_projection_and_bounded_parameters() -> None:
    path = CONTRACTS / "core-api.openapi.yaml"
    matches = [op for _, _, op in operations(path) if op["operationId"] == "readDataProductRows"]
    assert len(matches) == 1 and matches[0]["x-priority"] == "P0"
    parameters = {
        p["name"]: p for p in matches[0]["parameters"] if p.get("in") == "query"
    }
    assert parameters["contract_version_id"]["required"]
    assert parameters["columns"]["required"] and parameters["columns"]["explode"] is False
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert parameters["offset"]["schema"]["maximum"] == 10000
    response_schema = matches[0]["responses"]["200"]["content"]["application/json"]["schema"]
    row_example = next(c["payload"] for c in CASES if c["name"] == "governed-data-rows")
    validator(response_schema, path).validate(row_example)


def test_workflow_lookup_can_recover_without_known_link_id() -> None:
    path = CONTRACTS / "platform-api.openapi.yaml"
    lookup = next(op for _, _, op in operations(path) if op["operationId"] == "findWorkflowLink")
    assert {p["name"] for p in lookup["parameters"] if p.get("required")} == {
        "application_id", "workflow_key", "business_key", "run_number",
    }
    assert all(p.get("in") != "path" for p in lookup["parameters"])
    assert "404" in lookup["responses"]
