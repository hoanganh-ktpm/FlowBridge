# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Offline structural checks for the Flowable runtime compose file.

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_flowable_compose_is_minimal_and_isolated():
    compose = yaml.safe_load(
        (ROOT / "infra/flowable/docker-compose.flowable.yml").read_text()
    )
    assert set(compose["services"]) == {"flowable"}
    service = compose["services"]["flowable"]
    assert service["image"].startswith("flowable/flowable-rest:7.2.0")
    assert all(port.startswith("127.0.0.1:") for port in service.get("ports", []))
    assert "privileged" not in service and "container_name" not in service
    env = service["environment"]
    assert "SPRING_DATASOURCE_URL" in env
    assert "FLOWABLE_DATABASE_SCHEMA" in env
    assert service.get("healthcheck", {}).get("test")
    assert "host.docker.internal" in str(service)
