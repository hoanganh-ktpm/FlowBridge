# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Launch the standard app with an explicit generated identity profile and the existing DB settings.
import argparse
import json
from pathlib import Path

import uvicorn

from flowbridge.main import create_app
from flowbridge.settings import Settings


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run API with local SSO profile; no fake identity")
    parser.add_argument("--identity-dir", type=Path, default=root / ".cache/identity")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    runtime = json.loads((args.identity_dir / "runtime.json").read_text("utf-8"))
    settings = Settings(_env_file=root / ".env", **runtime)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
