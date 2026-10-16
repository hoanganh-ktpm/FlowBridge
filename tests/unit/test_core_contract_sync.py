# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import hashlib
import runpy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sync = runpy.run_path(str(ROOT / "scripts/sync_core_contracts.py"))["sync_contracts"]


def test_core_snapshot_manifest_and_draft_provenance() -> None:
    source = ROOT.parent / "ho-so-olp/03-api/contracts"
    # Source archives remain verifiable without the author workspace.
    sync(source if source.is_dir() else ROOT / "contracts")


@pytest.mark.parametrize("drift", ["schema", "version", "draft", "checksum"])
def test_sync_rejects_unreviewed_drift_before_writing(tmp_path: Path, drift: str) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    for name in (
        "opencore-api.yaml",
        "supplier-assistant-layer2-api.yaml",
        "core-contracts.sha256",
    ):
        (destination / name).write_bytes((ROOT / "contracts" / name).read_bytes())
    content = (destination / "opencore-api.yaml").read_bytes()
    (source / "opencore-api.yaml").write_bytes(content)
    if drift in {"schema", "version"}:
        document = yaml.safe_load(content)
        if drift == "schema":
            document["components"]["schemas"]["Id"]["maxLength"] = 129
        else:
            document["info"]["version"] = "999.0.0"
        (source / "opencore-api.yaml").write_text(yaml.safe_dump(document), "utf-8")
    elif drift == "draft":
        path = destination / "supplier-assistant-layer2-api.yaml"
        document = yaml.safe_load(path.read_bytes())
        document["components"]["schemas"]["Id"]["maxLength"] = 129
        path.write_text(yaml.safe_dump(document), "utf-8")
    else:
        (destination / "core-contracts.sha256").write_text("bad checksum\n", "utf-8")
    before = {path.name: path.read_bytes() for path in destination.iterdir()}
    with pytest.raises(ValueError):
        sync(source, destination=destination, write=drift != "checksum")
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == before


def test_sync_repairs_reviewed_dialect_and_provenance(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    canonical = (ROOT / "contracts/opencore-api.yaml").read_bytes()
    (source / "opencore-api.yaml").write_bytes(canonical)
    document = yaml.safe_load(canonical)
    document["jsonSchemaDialect"] = "https://json-schema.org/draft/2020-12/schema"
    (destination / "opencore-api.yaml").write_text(yaml.safe_dump(document), "utf-8")
    (destination / "supplier-assistant-layer2-api.yaml").write_bytes(
        (ROOT / "contracts/supplier-assistant-layer2-api.yaml").read_bytes()
    )
    sync(source, destination=destination, write=True)
    sync(source, destination=destination)
    assert (destination / "opencore-api.yaml").read_bytes() == canonical
    draft = yaml.safe_load((destination / "supplier-assistant-layer2-api.yaml").read_bytes())
    assert draft["x-upstream"]["sha256"] == hashlib.sha256(canonical).hexdigest()
