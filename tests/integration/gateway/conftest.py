# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
import os
import ssl
import time

import httpx
import pytest


@pytest.fixture(scope="session")
def gateway() -> httpx.Client:
    if os.environ.get("FLOWBRIDGE_GATEWAY_TEST") != "true":
        pytest.fail("Gateway acceptance requires the isolated real-engine profile; not skipped")
    ca = os.environ["FLOWBRIDGE_GATEWAY_CA"]
    with httpx.Client(
        base_url="https://gateway:9443",
        verify=ssl.create_default_context(cafile=ca),
        trust_env=False,
        timeout=8,
    ) as client:
        deadline = time.monotonic() + 60
        while True:
            try:
                if client.get("/api/v1/core/identity/config").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("Real APISIX/API did not become ready")
            time.sleep(1)
        yield client
