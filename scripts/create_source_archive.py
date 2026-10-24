"""Create a clean ZIP from a verified Hatch source distribution, never from workspace."""

import argparse
import tarfile
import zipfile
from pathlib import Path

from scripts.verify_source_archive import verify


def create(source: Path, target: Path):
    problems = verify(source)
    if problems:
        raise ValueError("source distribution failed verification: " + "; ".join(problems))
    if target.exists():
        raise ValueError("target archive already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        tarfile.open(source, "r:gz") as archive,
        zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as output,
    ):
        for member in archive:
            if not member.isfile():
                continue
            data = archive.extractfile(member)
            if data is None:
                continue
            with data:
                output.writestr(member.name, data.read())
    problems = verify(target)
    if problems:
        raise ValueError("ZIP verification failed: " + "; ".join(problems))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdist", type=Path)
    parser.add_argument("zip", type=Path)
    args = parser.parse_args(argv)
    try:
        create(args.sdist, args.zip)
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"FAIL: {type(exc).__name__}")
        return 1
    print(f"PASS: clean ZIP at {args.zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
