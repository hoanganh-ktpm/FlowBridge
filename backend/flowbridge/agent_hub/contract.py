# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker


@lru_cache
def document() -> dict:
    packaged = Path(__file__).parent / "contracts"
    root = packaged if packaged.exists() else Path(__file__).resolve().parents[3] / "contracts"

    def read(path: Path) -> Any:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def expand(value: Any, path: Path, local: dict) -> Any:
        if isinstance(value, list):
            return [expand(item, path, local) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            filename, _, pointer = value["$ref"].partition("#")
            target_path = (path.parent / filename).resolve() if filename else path
            target = read(target_path) if filename else local
            selected = target
            for part in pointer.strip("/").split("/") if pointer else []:
                selected = selected[part.replace("~1", "/").replace("~0", "~")]
            return expand(selected, target_path, target)
        return {key: expand(item, path, local) for key, item in value.items()}

    path = root / "agent-hub-api.yaml"
    original = read(path)
    return expand(original, path, original)


@lru_cache
def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        document()["components"]["schemas"][name], format_checker=FormatChecker()
    )


def validate(value: Any, name: str) -> None:
    validator(name).validate(value)


def openapi_fragment() -> dict:
    # Fully resolved refs also work in a wheel with no source checkout or remote resolver.
    doc = deepcopy(document())
    for entry in doc["paths"].values():
        for method, operation in entry.items():
            if method in {"get", "post", "patch"}:
                operation["security"] = doc["security"]
    return {
        "paths": doc["paths"],
        "components": {"securitySchemes": doc["components"]["securitySchemes"]},
    }


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


class HubError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        self.status, self.code, self.message, self.retryable = status, code, message, retryable
        super().__init__(code)


def check(condition: Any, status: int, code: str, message: str) -> None:
    if not condition:
        raise HubError(status, code, message)
