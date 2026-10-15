# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Gọi HTTP server đã chạy để nghiệm thu API mẫu; không khởi động engine/dependency.

import argparse
import json
import re
from pathlib import Path

import httpx

from flowbridge.shared.contract import CONTRACT_URI, contract_document, operations, schema_validator


def check_api(base_url: str, requests_file: Path) -> int:
    # Đối chiếu requests.json với contract rồi gọi HTTP kiểm status/schema/header.
    examples = json.loads(requests_file.read_text(encoding="utf-8"))
    operation_index = {operation.operation_id: operation for operation in operations()}
    identifiers = [example["operationId"] for example in examples]
    if len(identifiers) != len(set(identifiers)) or set(identifiers) != set(operation_index):
        raise AssertionError("Request examples must cover each contract operation exactly once")
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as client:
        docs = client.get("/openapi.json")
        docs.raise_for_status()
        if docs.json().get("x-sample-mode") is not True:
            raise AssertionError("Target must expose the sample-mode schema")
        for example in examples:
            operation = operation_index[example["operationId"]]
            pattern = re.sub(r"\{[^}]+\}", "[^/]+", operation.path)
            if example["method"] != operation.method or not re.fullmatch(pattern, example["path"]):
                raise AssertionError("Example method/path differs from its contract operation")
            kwargs = {key: example[key] for key in ("json", "params", "data") if key in example}
            if "upload" in example:
                upload = example["upload"]
                kwargs["files"] = {
                    "file": (
                        upload["filename"],
                        upload["content"].encode("utf-8"),
                        upload["content_type"],
                    )
                }
            response = client.request(
                operation.method,
                example["path"],
                headers={"Idempotency-Key": "sample-check"},
                **kwargs,
            )
            if response.status_code != operation.status_code:
                raise AssertionError(
                    f"{operation.operation_id}: unexpected HTTP {response.status_code}"
                )
            spec = contract_document()["paths"][operation.path][operation.method.lower()]
            content = spec["responses"][str(operation.status_code)].get("content", {})
            if "application/json" in content:
                ref = content["application/json"]["schema"]["$ref"]
                schema_validator({"$ref": CONTRACT_URI + ref}).validate(response.json())
                if not response.headers.get("content-type", "").startswith("application/json"):
                    raise AssertionError("JSON response content type is missing")
            elif operation.status_code == 204:
                if response.content:
                    raise AssertionError("204 response must have no body")
            elif not response.content:
                raise AssertionError("Download must return sample bytes")
            if not response.headers.get("x-correlation-id"):
                raise AssertionError("Correlation header is missing")
    return len(examples)


def main() -> None:
    # CLI nghiệm thu HTTP, in số operation đạt sau khi kiểm đủ 28 mẫu.
    parser = argparse.ArgumentParser(description="Verify all sample API HTTP responses")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=Path, default=Path("examples/sample-api/requests.json"))
    args = parser.parse_args()
    print(f"PASS: {check_api(args.base_url, args.requests)} sample HTTP operations")


if __name__ == "__main__":
    main()
