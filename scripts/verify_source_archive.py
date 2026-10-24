"""Reject deployment source archives containing private or generated artifacts."""

import argparse
import stat
import tarfile
import zipfile
from pathlib import PurePosixPath

REQUIRED = {
    "deployment.example.yaml",
    "pyproject.toml",
    "uv.lock",
    "LICENSE",
    "scripts/deployment_cli.py",
    "scripts/deployment_smoke.py",
    "scripts/deployment_backup.py",
    "scripts/create_source_archive.py",
    "scripts/deployment_setup.py",
    "scripts/build_deployment_installer.py",
    "infra/deployment/keycloak_bootstrap.py",
    "docs/DEPLOYMENT.md",
}
PRIVATE_PARTS = {
    ".git",
    ".venv",
    ".tmp",
    ".cache",
    "node_modules",
    "secrets",
    "generated",
    "__pycache__",
    "dist",
    "build",
    "backups",
}
PRIVATE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".crt")


def verify(path):
    found = set()
    problems = []
    if str(path).lower().endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            members = [
                (member.filename, stat.S_ISLNK(member.external_attr >> 16))
                for member in archive.infolist()
            ]
    else:
        with tarfile.open(path, "r:gz") as archive:
            members = [(member.name, member.issym() or member.islnk()) for member in archive]
    for name, link in members:
        parts = PurePosixPath(name.replace("\\", "/")).parts
        relative = PurePosixPath(*parts[1:]) if len(parts) > 1 else PurePosixPath()
        if link or name.startswith("/") or ".." in parts:
            problems.append("unsafe archive member")
        if any(part in PRIVATE_PARTS for part in relative.parts):
            problems.append("private directory included")
        filename = relative.name.lower()
        if filename == ".env" or (filename.startswith(".env.") and filename != ".env.example"):
            problems.append("environment file included")
        if filename.endswith(PRIVATE_SUFFIXES):
            problems.append("private certificate/key included")
        found.add(relative.as_posix())
    missing = REQUIRED - found
    if missing:
        problems.append("required deployment files missing: " + ", ".join(sorted(missing)))
    return sorted(set(problems))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    args = parser.parse_args(argv)
    try:
        problems = verify(args.archive)
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError):
        problems = ["archive cannot be read"]
    if problems:
        print("FAIL: " + "; ".join(problems))
        return 1
    print("PASS: clean deployment source archive")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
