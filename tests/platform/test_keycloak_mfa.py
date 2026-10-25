"""Offline Admin API boundary tests; real OTP/browser coverage is opt-in mfa.mjs."""

import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from infra.deployment.keycloak_mfa import MfaError, configure_mfa

TEMPLATE = json.loads(
    (Path(__file__).parents[2] / "infra/keycloak/realm.template.json").read_text()
)


class Keycloak:
    def __init__(self):
        self.flows = {}
        self.executions = {}
        self.configs = {}
        self.writes = []
        self.web = {
            "id": "web-id",
            "clientId": "flowbridge-web-a",
            "publicClient": True,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": True,
            "redirectUris": ["https://portal.example.invalid/"],
            "webOrigins": ["https://portal.example.invalid"],
            "attributes": {"unrelated": "preserved"},
            "authenticationFlowBindingOverrides": {"direct_grant": "old-binding"},
            "protocolMappers": [],
        }
        self.realm = {
            key: TEMPLATE[key]
            for key in ("otpPolicyType", "otpPolicyAlgorithm", "otpPolicyDigits", "otpPolicyPeriod")
        }
        self.version = "26.8.1"

    def handle(self, request):
        path = request.url.path.partition("/admin/realms/test")[2]
        body = json.loads(request.content) if request.content else None
        if request.method != "GET":
            self.writes.append((request.method, path))
        if request.url.path == "/admin/serverinfo":
            result = {"systemInfo": {"version": self.version}}
        elif not path:
            result = self.realm
        elif path == "/clients":
            result = [self.web]
        elif path == "/clients/web-id":
            if request.method == "PUT":
                self.web.update(body)
            result = self.web
        elif path == "/clients/web-id/protocol-mappers/models":
            if request.method == "POST":
                self.web["protocolMappers"].append(body)
            result = self.web["protocolMappers"]
        elif path.endswith("/required-actions/CONFIGURE_TOTP"):
            result = {"enabled": True}
        elif path == "/authentication/flows":
            if request.method == "POST":
                self.flows[body["alias"]] = {**body, "id": body["alias"]}
                self.executions[body["alias"]] = []
            result = list(self.flows.values())
        elif path.startswith("/authentication/flows/"):
            alias = path.split("/")[3]
            if path.endswith("/executions/flow"):
                self.flows[body["alias"]] = {
                    **body,
                    "id": body["alias"],
                    "providerId": "basic-flow",
                }
                self.executions[body["alias"]] = []
                self.executions[alias].append(
                    {
                        "id": body["alias"],
                        "flowId": body["alias"],
                        "level": 0,
                        "requirement": "DISABLED",
                    }
                )
            elif path.endswith("/executions/execution"):
                self.executions[alias].append(
                    {
                        "id": body["provider"],
                        "providerId": body["provider"],
                        "level": 0,
                        "requirement": "DISABLED",
                    }
                )
            elif path.endswith("/executions"):
                if request.method == "PUT":
                    self.executions[alias] = [
                        body if e["id"] == body["id"] else e for e in self.executions[alias]
                    ]
                result = self.executions[alias]
                return httpx.Response(200, json=result)
            else:
                return httpx.Response(200, json=self.flows[alias])
            result = None
        elif path.startswith("/authentication/executions/") and path.endswith("/config"):
            identifier = path.split("/")[3]
            self.configs[identifier] = body
            for entries in self.executions.values():
                for entry in entries:
                    if entry["id"] == identifier:
                        entry["authenticationConfig"] = identifier
            result = None
        elif path.startswith("/authentication/config/"):
            result = self.configs[path.split("/")[-1]]
        else:
            raise AssertionError("Unexpected Keycloak request: " + path)
        return httpx.Response(200, json=result) if result is not None else httpx.Response(204)


def test_scoped_mfa_is_read_only_by_default_additive_and_idempotent():
    server = Keycloak()
    original = deepcopy(server.web)
    with httpx.Client(transport=httpx.MockTransport(server.handle)) as client:
        assert not configure_mfa(client, "https://idp.test", "test", "flowbridge-web-a")["enforced"]
        assert not server.writes
        assert configure_mfa(client, "https://idp.test", "test", "flowbridge-web-a", apply=True)[
            "enforced"
        ]
        writes = list(server.writes)
        assert not configure_mfa(
            client, "https://idp.test", "test", "flowbridge-web-a", apply=True
        )["changed"]
        assert writes == server.writes
    assert server.web["redirectUris"] == original["redirectUris"]
    assert server.web["webOrigins"] == original["webOrigins"]
    assert server.web["attributes"]["unrelated"] == "preserved"
    assert server.web["authenticationFlowBindingOverrides"]["direct_grant"] == "old-binding"
    assert server.web["attributes"]["minimum.acr.value"] == "2"
    assert not server.web["directAccessGrantsEnabled"]
    assert all("/users" not in path and "logout" not in path for _, path in server.writes)
    assert ("PUT", "") not in server.writes  # No realm-wide binding, policy or user changes.


@pytest.mark.parametrize("tamper", ["otp", "extra", "loa", "mapper"])
def test_existing_conflicting_flow_fails_without_mutation(tamper):
    server = Keycloak()
    with httpx.Client(transport=httpx.MockTransport(server.handle)) as client:
        configure_mfa(client, "https://idp.test", "test", "flowbridge-web-a", apply=True)
        entries = server.executions["flowbridge-password-totp-v2"]
        if tamper == "otp":
            entries[-1]["requirement"] = "ALTERNATIVE"
        elif tamper == "extra":
            entries.append(
                {
                    "id": "other",
                    "providerId": "auth-cookie",
                    "level": 0,
                    "requirement": "ALTERNATIVE",
                }
            )
        elif tamper == "loa":
            server.configs["conditional-level-of-authentication"]["config"][
                "loa-condition-level"
            ] = "1"
        else:
            server.web["protocolMappers"][0]["protocolMapper"] = "oidc-hardcoded-claim-mapper"
        server.writes.clear()
        with pytest.raises(MfaError, match="conflicts"):
            configure_mfa(client, "https://idp.test", "test", "flowbridge-web-a", apply=True)
        assert not server.writes


def test_unsupported_version_and_global_otp_policy_are_not_modified():
    server = Keycloak()
    with httpx.Client(transport=httpx.MockTransport(server.handle)) as client:
        server.version = "25.0.0"
        with pytest.raises(MfaError, match="26.8.1"):
            configure_mfa(client, "https://idp.test", "test", "flowbridge-web-a", apply=True)
        server.version = "26.8.1"
        server.realm["otpPolicyDigits"] = 8
        with pytest.raises(MfaError, match="OTP policy differs"):
            configure_mfa(client, "https://idp.test", "test", "flowbridge-web-a", apply=True)
        assert not server.writes
