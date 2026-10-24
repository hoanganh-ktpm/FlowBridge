"""Build a host-OS standalone installer from the deployment CLI."""

import hashlib
import subprocess
import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]


def main():
    name = "flowbridge-setup"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        "--name",
        name,
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / ".tmp" / "pyinstaller" / "build"),
        "--specpath",
        str(ROOT / ".tmp" / "pyinstaller"),
        "--paths",
        str(ROOT),
        "--add-data",
        f"{ROOT / 'deployment.example.yaml'}{':' if sys.platform != 'win32' else ';'}.",
        "--hidden-import",
        "questionary",
        str(ROOT / "scripts" / "deployment_setup.py"),
    ]
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode:
        return result.returncode
    artifact = ROOT / "dist" / (name + (".exe" if sys.platform == "win32" else ""))
    if not artifact.is_file():
        print("FAIL: installer artifact missing", file=sys.stderr)
        return 1
    members = set(CArchiveReader(str(artifact)).toc)
    if "deployment.example.yaml" not in members or any(
        part in name.lower().replace("\\", "/").split("/")
        for name in members
        for part in (".env", "secrets", ".tmp", "node_modules", "generated")
    ):
        print("FAIL: installer resources are incomplete or contain private files", file=sys.stderr)
        return 1
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum = artifact.with_name(artifact.name + ".sha256")
    checksum.write_text(f"{digest}  {artifact.name}\n", encoding="ascii")
    print(f"PASS: {artifact.name}; SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
