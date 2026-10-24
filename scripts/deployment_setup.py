"""Standalone deployment installer entrypoint; keep Core's `flowbridge` untouched."""

from scripts.deployment_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
