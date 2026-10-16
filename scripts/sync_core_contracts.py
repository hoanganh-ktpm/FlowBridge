# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Check semantic compatibility before copying the canonical Core contract or rebinding a draft.
import argparse
import hashlib
import re
from pathlib import Path

import yaml
from fastapi.openapi.models import OpenAPI
from jsonschema import Draft202012Validator


def sync_contracts(source: Path, *, write: bool = False, destination: Path | None = None) -> None:
    destination = destination or Path(__file__).resolve().parents[1] / "contracts"
    content = (source / "opencore-api.yaml").read_bytes()
    canonical = yaml.safe_load(content)
    target = destination / "opencore-api.yaml"
    existing = yaml.safe_load(target.read_bytes())
    draft_path = destination / "supplier-assistant-layer2-api.yaml"
    draft_text = draft_path.read_text("utf-8")
    draft = yaml.safe_load(draft_text)
    OpenAPI.model_validate(canonical)
    for schema in canonical["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)
    # Dialect metadata is the reviewed correction; other semantic drift needs explicit review.
    for document in (existing, canonical):
        document.pop("jsonSchemaDialect", None)
    if existing != canonical:
        raise ValueError("Core semantic drift requires review before sync")
    upstream = draft["x-upstream"]
    if upstream["version"] != canonical["info"]["version"]:
        raise ValueError("Draft upstream version differs")
    for name in upstream["shared_components"]:
        if draft["components"]["schemas"][name] != canonical["components"]["schemas"][name]:
            raise ValueError(f"Draft shared schema differs: {name}")
    digest = hashlib.sha256(content).hexdigest()
    manifest = destination / "core-contracts.sha256"
    expected = f"{digest}  opencore-api.yaml\n"
    if write:
        replacement, count = re.subn(
            r"(?m)^  sha256: [a-f0-9]{64}$", f"  sha256: {digest}", draft_text
        )
        if count != 1:
            raise ValueError("Expected exactly one draft upstream checksum")
        target.write_bytes(content)
        draft_path.write_text(replacement, encoding="utf-8", newline="\n")
        manifest.write_text(expected, encoding="utf-8", newline="\n")
    elif (
        target.read_bytes() != content
        or upstream["sha256"] != digest
        or not manifest.is_file()
        or manifest.read_text("utf-8") != expected
    ):
        raise ValueError("Core snapshot, manifest or draft provenance differs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check or sync Core snapshot and draft provenance")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    sync_contracts(args.source, write=args.write)
    print("PASS: Core contract snapshot, checksum and draft shared schemas/provenance")


if __name__ == "__main__":
    main()
