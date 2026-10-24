# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in real browser against the existing local Keycloak browser client, without realm edits."""

import os
import subprocess
import sys
import time

import httpx
import pytest
from scripts.e2e_environment import ROOT
from tests.e2e.test_data_workspace import live_workspace  # noqa: F401 -- shared real fixture

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("FLOWBRIDGE_WORKSPACE_BROWSER_E2E") != "1",
        reason="Opt-in real browser SSO",
    ),
]


@pytest.mark.usefixtures("live_workspace")
def test_real_browser_data_workspace():
    # This fixture owns only the Portal process it starts; Core/engines stay running.
    with (ROOT / ".tmp/data-workspace/portal-live.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "scripts.data_workspace",
                "serve-portal",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            for _ in range(80):
                assert process.poll() is None, "Portal startup failed; inspect private local log"
                try:
                    if httpx.get("http://127.0.0.1:5174/config.json", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            result = subprocess.run(
                ["node", "tests/live/data-workspace.mjs"],
                cwd=ROOT / "portal",
                capture_output=True,
                text=True,
                timeout=300,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                process.terminate()
            process.wait(timeout=10)
