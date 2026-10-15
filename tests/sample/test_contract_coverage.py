# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Kiểm bao phủ 28 operation và mapping gateway theo contract chuẩn.

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from flowbridge.gateway import route_map
from flowbridge.main import create_app
from flowbridge.settings import Settings
from flowbridge.shared.contract import CONTRACT_URI, contract_document, operations, schema_validator


def test_runtime_routes_cover_the_contract_and_gateway_mapping() -> None:
    app = create_app(Settings(_env_file=None, sample_mode=True, database_url=None))
    # Gọi bộ sinh framework trước sample override để kiểm tra route thực đã đăng ký.
    generated = FastAPI.openapi(app)
    actual = {
        (method.upper(), path, spec["operationId"])
        for path, entry in generated["paths"].items()
        for method, spec in entry.items()
    }
    expected = {(op.method, op.path, op.operation_id) for op in operations()}
    assert len(expected) == 28
    assert actual == expected
    for op in operations():
        spec = generated["paths"][op.path][op.method.lower()]
        assert str(op.status_code) in spec["responses"]
    routes = route_map("https://sample.example.invalid")
    assert {(r.method, r.path, r.operation_id) for r in routes} == expected
    assert len(routes) == len({(r.method, r.path) for r in routes})
    assert all(
        r.visibility == ("internal" if r.path.startswith("/internal/") else "public")
        for r in routes
    )
    assert all("_ops" not in r.path and "admin" not in r.path for r in routes)
    assert all(r.upstream == "https://sample.example.invalid" for r in routes)


@pytest.mark.parametrize("url", ["ftp://bad", "https://u:p@host", "https://host/admin", "x"])
def test_gateway_mapping_rejects_invalid_upstreams(url: str) -> None:
    with pytest.raises(ValueError):
        route_map(url)


def test_contract_snapshot_matches_provenance_checksum() -> None:
    snapshot = files("flowbridge").joinpath("contracts/opencore-api.yaml").read_bytes()
    assert hashlib.sha256(snapshot).hexdigest() == (
        "3cd8cd7e97f5e018aeadaca55952c07d9d1d93d2e40797cb2641298ee0d376f2"
    )
    # Khi chạy ở workspace gốc, so sánh luôn nguồn chuẩn; archive sạch dùng checksum trên.
    upstream = Path(__file__).resolve().parents[3] / "03-api/contracts/opencore-api.yaml"
    if upstream.is_file():
        assert snapshot == upstream.read_bytes()


def test_published_examples_cover_valid_requests_for_every_operation() -> None:
    request_file = Path(__file__).resolve().parents[2] / "examples/sample-api/requests.json"
    examples = json.loads(request_file.read_text(encoding="utf-8"))
    index = {operation.operation_id: operation for operation in operations()}
    assert len(examples) == len(index) == 28
    assert {example["operationId"] for example in examples} == set(index)
    for example in examples:
        operation = index[example["operationId"]]
        assert example["method"] == operation.method
        assert re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", operation.path), example["path"])
        if "json" in example:
            pointer_path = operation.path.replace("~", "~0").replace("/", "~1")
            ref = (
                f"{CONTRACT_URI}#/paths/{pointer_path}/{operation.method.lower()}"
                "/requestBody/content/application~1json/schema"
            )
            errors = list(schema_validator({"$ref": ref}).iter_errors(example["json"]))
            assert not errors, [error.message for error in errors]


@pytest.mark.asyncio
async def test_sample_docs_declare_auth_and_persistence_simulation(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["x-sample-mode"] is True
    assert "without persistence" in schema["info"]["description"]
    assert schema["security"] == []
    canonical = contract_document()
    assert schema["components"] == canonical["components"]
    for op in operations():
        spec = schema["paths"][op.path][op.method.lower()]
        original = canonical["paths"][op.path][op.method.lower()]
        assert spec["security"] == []
        assert spec["x-production-security"] == original.get("security", canonical["security"])
        assert spec["responses"] == original["responses"]
        assert spec.get("requestBody") == original.get("requestBody")
    assert contract_document() == canonical
    assert (await client.get("/docs")).status_code == 200


def validate_response(response: httpx.Response, dto: str) -> None:
    validator = schema_validator({"$ref": f"{CONTRACT_URI}#/components/schemas/{dto}"})
    errors = list(validator.iter_errors(response.json()))
    assert not errors, [(list(error.path), error.message) for error in errors]
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-correlation-id"]
