# SPDX-License-Identifier: GPL-3.0-or-later
from scripts.decision_local_identity import client_definition


def test_local_api_client_cannot_use_browser_or_password_grants():
    value = client_definition("decision-test-owned-sales", "synthetic-only")
    assert value["serviceAccountsEnabled"] is True
    assert value["standardFlowEnabled"] is False
    assert value["directAccessGrantsEnabled"] is False
    assert value["publicClient"] is False
    assert "authenticationFlowBindingOverrides" not in value
    assert "minimum.acr.value" not in value
    assert value["protocolMappers"][0]["config"]["included.client.audience"] == "flowbridge-api"
