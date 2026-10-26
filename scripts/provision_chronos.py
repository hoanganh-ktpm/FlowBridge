# SPDX-License-Identifier: GPL-3.0-or-later
"""Provision the official pretrained Chronos-2 artifact, never business data."""

import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "amazon/chronos-2"
REVISION = "29ec3766d36d6f73f0696f85560a422f50e8498c"
LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0.txt"


def download(url, target, expected_hash=None):
    if target.exists() and expected_hash:
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == expected_hash:
                return expected_hash
    temporary = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "FlowBridge-model-provisioner"})
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as stream:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            stream.write(chunk)
    actual = digest.hexdigest()
    if expected_hash and actual != expected_hash:
        raise ValueError(f"Official artifact checksum mismatch: {target.name}")
    temporary.replace(target)
    return actual


def main():
    directory = ROOT / ".cache/models/chronos-2" / REVISION
    directory.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/api/models/{REPOSITORY}/revision/{REVISION}?blobs=true"
    with urllib.request.urlopen(url, timeout=60) as response:
        metadata = json.load(response)
    if metadata["sha"] != REVISION:
        raise ValueError("Model revision differs from the pinned release")
    files = {item["rfilename"]: item for item in metadata["siblings"]}
    names = ["README.md", "config.json", "model.safetensors"]
    hashes = {}
    for name in names:
        print(f"Provisioning {REPOSITORY}@{REVISION[:12]} / {name}", flush=True)
        hashes[name] = download(
            f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}?download=true",
            directory / name,
            files[name].get("lfs", {}).get("sha256"),
        )
    if not re.search(
        r"(?m)^license:\s*apache-2\.0\s*$",
        (directory / "README.md").read_text(encoding="utf-8"),
    ):
        raise ValueError("Official model card does not declare the expected weights license")
    # The model repository declares the license in its model card. Bundle the
    # canonical license text separately and record its actual source explicitly.
    hashes["LICENSE"] = download(LICENSE_URL, directory / "LICENSE")
    manifest = {
        "repository": REPOSITORY,
        "revision": REVISION,
        "weights_license": "Apache-2.0",
        "weights_license_source": f"https://huggingface.co/{REPOSITORY}/blob/{REVISION}/README.md",
        "license_text_source": LICENSE_URL,
        "sha256": hashes,
    }
    (directory / "model_provenance.json").write_text(json.dumps(manifest, indent=2) + "\n")
    local = ROOT / ".tmp/data-workspace"
    local.mkdir(parents=True, exist_ok=True)
    (local / "chronos.env").write_text(
        f"FLOWBRIDGE_WORKSPACE_CHRONOS_PATH='{directory.as_posix()}'\n"
        f"FLOWBRIDGE_WORKSPACE_CHRONOS_REVISION='{REVISION}'\n",
        encoding="utf-8",
    )
    print("Official pretrained model ready. No fine-tuning or business-data upload.", flush=True)


if __name__ == "__main__":
    main()
