import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin, urlparse

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
FIXTURES = Path(__file__).parent / "fixtures"
ENTRYPOINTS = sorted(CONTRACTS.glob("*.openapi.yaml"))
YAML_FILES = sorted(CONTRACTS.glob("*.yaml"))
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def read_document(path):
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.load(text, UniqueKeyLoader)


DOCUMENTS = {path.resolve().as_uri(): read_document(path) for path in YAML_FILES}
for path in (
    CONTRACTS / "identity-context.schema.json",
    CONTRACTS / "onboarding/application-manifest-v1.schema.json",
):
    DOCUMENTS[path.resolve().as_uri()] = read_document(path)

REGISTRY = Registry().with_resources(
    (uri, Resource.from_contents(document, default_specification=DRAFT202012))
    for uri, document in DOCUMENTS.items()
)
CASES = read_document(CONTRACTS / "fixtures/review-cases.json")["cases"]


def pointer(document, fragment):
    if not fragment:
        return document
    assert fragment.startswith("/"), fragment
    result = document
    for part in fragment[1:].split("/"):
        key = unquote(part).replace("~1", "/").replace("~0", "~")
        result = result[int(key)] if isinstance(result, list) else result[key]
    return result


def resolve_reference(reference, base_uri):
    uri, fragment = urldefrag(urljoin(base_uri, reference))
    assert uri in DOCUMENTS, f"Unregistered or external reference: {reference}"
    return pointer(DOCUMENTS[uri], fragment), uri


def bundle(value, base_uri, stack=()):
    if isinstance(value, list):
        return [bundle(item, base_uri, stack) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        absolute = urljoin(base_uri, value["$ref"])
        assert absolute not in stack, f"Unexpected recursive contract reference: {absolute}"
        target, target_uri = resolve_reference(value["$ref"], base_uri)
        resolved = bundle(target, target_uri, (*stack, absolute))
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if siblings:
            resolved = {**resolved, **bundle(siblings, base_uri, stack)}
        return resolved
    return {key: bundle(item, base_uri, stack) for key, item in value.items()}


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def operation_catalog():
    for path in ENTRYPOINTS:
        document = bundle(DOCUMENTS[path.resolve().as_uri()], path.resolve().as_uri())
        for route, path_item in document["paths"].items():
            for method, operation in path_item.items():
                if method in HTTP_METHODS:
                    yield path, route, method, operation, path_item


OPERATIONS = list(operation_catalog())
SCHEMAS = [
    (path.name, name, schema)
    for path in YAML_FILES
    for name, schema in DOCUMENTS[path.resolve().as_uri()]
    .get("components", {})
    .get("schemas", {})
    .items()
]


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda path: path.name)
def test_yaml_syntax_unique_keys_and_references(path):
    document = read_document(path)
    assert re.fullmatch(r"[a-z][a-z0-9-]*(?:\.openapi)?\.yaml", path.name)
    for node in walk(document):
        if "$ref" in node:
            resolve_reference(node["$ref"], path.resolve().as_uri())


@pytest.mark.parametrize("path", ENTRYPOINTS, ids=lambda path: path.name)
def test_openapi_entrypoint_bundles_and_validates_offline(path):
    document = bundle(DOCUMENTS[path.resolve().as_uri()], path.resolve().as_uri())
    official_schema = read_document(FIXTURES / "openapi-3.1.schema.json")
    errors = list(Draft202012Validator(official_schema).iter_errors(document))
    assert not errors, "\n".join(f"{list(error.path)}: {error.message}" for error in errors[:5])
    assert document["openapi"] == "3.1.1"
    assert document["info"]["version"] == "0.2.0"
    assert not any("$ref" in node for node in walk(document))


@pytest.mark.parametrize(
    "filename,name,schema", SCHEMAS, ids=[f"{item[0]}:{item[1]}" for item in SCHEMAS]
)
def test_component_json_schema_is_well_formed(filename, name, schema):
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_synthetic_fixture_shape(case):
    schema_uri = urljoin(CONTRACTS.as_uri() + "/", case["schema"])
    validator = Draft202012Validator(
        {"$ref": schema_uri}, registry=REGISTRY, format_checker=FormatChecker()
    )
    errors = list(validator.iter_errors(case["value"]))
    assert (not errors) == case["valid"], [error.message for error in errors[:3]]


def test_preserves_legacy_http_paths_and_operation_ids():
    legacy = read_document(FIXTURES / "legacy-operations-v0.1.1.json")["operations"]
    actual = {
        (route, method): operation["operationId"] for _, route, method, operation, _ in OPERATIONS
    }
    assert len(legacy) == 84
    for previous in legacy:
        assert actual[(previous["path"], previous["method"])] == previous["operationId"]
    assert len(actual) == 96  # 95 existing operations + grant-filtered Workspace discovery.


def test_operation_ids_service_boundaries_and_priorities():
    identifiers = [operation["operationId"] for _, _, _, operation, _ in OPERATIONS]
    assert len(identifiers) == len(set(identifiers))
    priorities = Counter(operation["x-priority"] for _, _, _, operation, _ in OPERATIONS)
    assert priorities == {"P0": 56, "P1": 40}
    for path, route, _, operation, _ in OPERATIONS:
        if path.name in {
            "sales-api.openapi.yaml",
            "warehouse-api.openapi.yaml",
            "logistics-api.openapi.yaml",
        }:
            assert route.startswith("/api/v1/" + path.name.split("-")[0] + "/")
            assert operation["x-priority"] == "P1"
        assert operation["x-database-alignment"] == "PENDING_DB_ALIGNMENT"
        assert operation["x-authorization"]


@pytest.mark.parametrize("item", OPERATIONS, ids=lambda item: item[3]["operationId"])
def test_http_request_response_security_and_parameter_consistency(item):
    path, route, method, operation, path_item = item
    original = DOCUMENTS[path.resolve().as_uri()]
    assert original["security"] == [{"bearerAuth": []}]
    assert original["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    parameters = [*path_item.get("parameters", []), *operation.get("parameters", [])]
    pairs = [(parameter["in"], parameter["name"]) for parameter in parameters]
    assert len(pairs) == len(set(pairs))
    assert {parameter["name"] for parameter in parameters if parameter["in"] == "path"} == set(
        re.findall(r"\{([^}]+)\}", route)
    )
    assert all(parameter["required"] for parameter in parameters if parameter["in"] == "path")
    assert ("header", "X-Correlation-Id") in pairs
    if method in {"post", "patch", "put"}:
        body = operation["requestBody"]["content"]["application/json"]["schema"]
        assert body["type"] == "object" and body["additionalProperties"] is False
        if operation["x-idempotency"] != "NONE_READ_ONLY":
            assert ("header", "Idempotency-Key") in pairs
    else:
        assert "requestBody" not in operation
    assert all("$ref" not in response for response in operation["responses"].values())
    for status, response in operation["responses"].items():
        assert "X-Correlation-Id" in response["headers"]
        assert response["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
        assert "application/json" in response["content"]
        if str(status) == "202":
            assert {"Location", "Retry-After"} <= response["headers"].keys()


def test_http_read_projections_do_not_declare_control_plane_secrets():
    forbidden = {
        "password",
        "secret_ref",
        "host",
        "port",
        "database_name",
        "db_user",
        "query_text",
        "request_config_json",
        "enforcement_role",
    }
    for _, _, _, operation, _ in OPERATIONS:
        for response in operation["responses"].values():
            schema = response["content"]["application/json"]["schema"]
            for node in walk(schema):
                assert not forbidden.intersection(node.get("properties", {})), operation[
                    "operationId"
                ]


def test_canonical_annotations_preserve_manifest_aliases_and_relationships():
    submission = next(case["value"] for case in CASES if case["name"] == "onboard-warehouse")
    application = submission["manifest"]["application"]["key"]
    declarations = {product["key"]: product for product in submission["manifest"]["data_products"]}
    assert {
        proposal["product_key"] for proposal in submission["product_contracts"]
    } == declarations.keys()
    for proposal in submission["product_contracts"]:
        declaration = declarations[proposal["product_key"]]
        contract = proposal["contract_json"]
        assert proposal["version"] == declaration.get("contract_version", "1.0.0")
        assert [(field["column"], field["name"]) for field in contract["fields"]] == [
            (field["column"], field.get("alias", field["column"]))
            for field in declaration["fields"]
        ]
        assert set(contract["business_key"]) <= {field["name"] for field in contract["fields"]}
        for expected, actual in zip(
            declaration.get("relationships", []), contract["relationships"], strict=True
        ):
            assert actual["mapping"] == expected["mapping"]
            assert actual["target_product"] == application + "." + expected["target_product"]


def test_schema_ownership_and_manifest_reuse():
    names = [name for _, name, _ in SCHEMAS]
    assert len(names) == len(set(names)), "Duplicate shared schema definition"
    onboarding = DOCUMENTS[(CONTRACTS / "application-onboarding.yaml").as_uri()]
    assert onboarding["components"]["schemas"]["OnboardingSubmission"]["properties"][
        "manifest"
    ] == {"$ref": "./onboarding/application-manifest-v1.schema.json"}
    canonical = DOCUMENTS[(CONTRACTS / "data-contracts.yaml").as_uri()]["components"]["schemas"]
    assert {"column", "name", "physical_type", "wire_type", "nullable"} <= set(
        canonical["ContractField"]["required"]
    )
    assert {"grain", "business_key", "classification", "relationships"} <= set(
        canonical["DataContractDocument"]["required"]
    )


def test_draft_publication_and_governance_profile_are_explicit():
    document = bundle(
        DOCUMENTS[(CONTRACTS / "core-api.openapi.yaml").as_uri()],
        (CONTRACTS / "core-api.openapi.yaml").as_uri(),
    )
    patch = document["paths"]["/api/v1/core/data-contracts/{contract_version_id}"]["patch"]
    assert "DRAFT" in patch["description"] and "immutable" in patch["description"]
    grant = DOCUMENTS[(CONTRACTS / "data-governance.yaml").as_uri()]["components"]["schemas"][
        "Grant"
    ]
    assert grant["properties"]["consumer_principal_id"] == {"type": "null"}
    assert "desired_revision" in grant["properties"] and "applied_revision" in grant["properties"]


def test_schema_fixture_is_pinned():
    provenance = read_document(FIXTURES / "provenance.json")
    for filename, details in provenance["files"].items():
        assert hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest() == details["sha256"]
        assert urlparse(details["source"]).scheme == "https"


@pytest.mark.parametrize(
    "filename,schema,statuses",
    [
        ("core-api.openapi.yaml", "Source", "DRAFT|VALIDATING|CONNECTED|FAILED|DISABLED"),
        ("data-governance.yaml", "Product", "DRAFT|READY|PROVISIONING|ACTIVE|ERROR|DISABLED"),
        ("data-contracts.yaml", "DataContract", "DRAFT|PUBLISHED|RETIRED"),
        ("data-governance.yaml", "AccessRequest", "PENDING|APPROVED|REJECTED|CANCELLED"),
        ("data-governance.yaml", "Grant", "PENDING_APPLY|ACTIVE|REVOKING|REVOKED|EXPIRED|ERROR"),
        ("data-governance.yaml", "PublicationJob", "QUEUED|RUNNING|RETRY|SUCCEEDED|FAILED"),
    ],
)
def test_core_status_enums_align_with_reference_design(filename, schema, statuses):
    document = DOCUMENTS[(CONTRACTS / filename).as_uri()]
    assert document["components"]["schemas"][schema]["properties"]["status"][
        "enum"
    ] == statuses.split("|")


def test_error_details_remains_compatible_with_existing_boundary():
    document = DOCUMENTS[(CONTRACTS / "common.openapi.yaml").as_uri()]
    assert "details" not in document["components"]["schemas"]["Error"]["required"]


@pytest.mark.parametrize("item", OPERATIONS, ids=lambda item: item[3]["operationId"])
def test_three_layer_authorization_policy_matrix(item):
    _, route, method, operation, _ = item
    consumer_reads = {
        "/api/v1/core/me": "SELF_IDENTITY",
        "/api/v1/core/authorized-data-products": "DATA_GRANT",
        "/api/v1/core/data-products": "CATALOG_READ",
        "/api/v1/core/data-products/{data_product_id}": "CATALOG_READ",
        "/api/v1/core/data-products/{data_product_id}/contracts": "CATALOG_READ",
        "/api/v1/core/data-contracts/{contract_version_id}": "CATALOG_READ",
        "/api/v1/core/access-grants": "GRANT_STATUS",
        "/api/v1/core/access-grants/{access_grant_id}": "GRANT_STATUS",
        "/api/v1/core/data-products/{data_product_id}/rows": "DATA_GRANT",
    }
    if route.startswith("/api/v1/core/"):
        expected = (
            "DATA_GRANT" if route.endswith("/query")
            else consumer_reads.get(route, "IT_ADMIN") if method == "get" else "IT_ADMIN"
        )
    elif route == "/api/v1/platform/operations/execute":
        expected = "OPERATION_EXECUTE"
    elif route.startswith("/api/v1/platform/workflow-runs"):
        expected = "WORKFLOW_RESOURCE" if method == "post" else "WORKFLOW_READ"
    else:
        expected = "BUSINESS_RECEIVER"
    assert operation["x-authorization-policy"] == expected
    if expected == "IT_ADMIN":
        assert "platform_role=PLATFORM_ADMIN" in operation["x-authorization"]
        assert "No department OWNER membership" in operation["x-authorization"]
    elif expected in {"DATA_GRANT", "OPERATION_EXECUTE", "BUSINESS_RECEIVER"}:
        assert "PLATFORM_ADMIN" in operation["x-authorization"]


def test_department_owner_is_metadata_not_a_management_role():
    core = DOCUMENTS[(CONTRACTS / "core-api.openapi.yaml").as_uri()]["components"]["schemas"]
    role = core["ResolvedMembership"]["properties"]["membership_role"]
    assert set(role["enum"]) == {"STAFF", "OWNER"}
    assert "no DX-OS management" in role["description"]
    assert core["ResolvedIdentityContext"]["properties"]["platform_role"]["enum"] == [
        "NONE",
        "PLATFORM_ADMIN",
    ]
    consumer = next(case["value"] for case in CASES if case["name"] == "resolved-identity")
    assert consumer["platform_role"] == "NONE"
    assert consumer["memberships"][0]["membership_role"] == "OWNER"


def test_admin_configuration_is_not_a_consumer_read_grant():
    schemas = DOCUMENTS[(CONTRACTS / "application-onboarding.yaml").as_uri()]["components"][
        "schemas"
    ]
    requirements = schemas["ApprovalRequirement"]["properties"]["kind"]["enum"]
    assert "IT_PUBLICATION" in requirements
    assert "TARGET_CONFIGURATION_ACCESS" in requirements
    assert "OWNER_PUBLICATION" not in requirements
    assert "TARGET_READ_ACCESS" not in requirements
    consumer_read = next(item[3] for item in OPERATIONS if item[1].endswith("/rows"))
    assert consumer_read["x-authorization-policy"] == "DATA_GRANT"
    assert "Every caller, including IT" in consumer_read["x-authorization"]
