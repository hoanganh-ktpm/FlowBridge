# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Đồng bộ snapshot app/AI từ nguồn được chỉ định; mặc định chỉ kiểm tra drift.

import argparse
import hashlib
from pathlib import Path

import yaml


def sync_contracts(source: Path, *, write: bool = False) -> None:
    # Nguồn là directory chứa app-api.yaml và ai-tools-api.yaml, không hardcode workspace.
    destination = Path(__file__).resolve().parents[1] / "backend/src/flowbridge/contracts"
    checksums: list[str] = []
    schemas = None
    for name in ("app-api.yaml", "ai-tools-api.yaml"):
        content = (source / name).read_bytes()
        document = yaml.safe_load(content)
        if document["info"]["version"] != "0.4.1":
            raise ValueError("Expected application database contract 0.4.1")
        current = document["components"]["schemas"]
        if schemas is not None and current != schemas:
            raise ValueError("Application and tool DTO schemas must agree")
        schemas = current
        target = destination / name
        if write:
            target.write_bytes(content)
        elif not target.is_file() or target.read_bytes() != content:
            raise ValueError(f"Contract snapshot differs: {name}")
        checksums.append(f"{hashlib.sha256(content).hexdigest()}  {name}\n")
    manifest = destination / "application-contracts.sha256"
    expected = "".join(checksums)
    if write:
        manifest.write_text(expected, encoding="utf-8", newline="\n")
    elif manifest.read_text(encoding="utf-8") != expected:
        raise ValueError("Application contract checksum manifest differs")


def main() -> None:
    # Chạy từ bất kỳ directory; source được truyền tường minh.
    parser = argparse.ArgumentParser(description="Check or sync application contract snapshots")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    sync_contracts(args.source, write=args.write)
    print("PASS: application/tool contract snapshots and checksums")


if __name__ == "__main__":
    main()
