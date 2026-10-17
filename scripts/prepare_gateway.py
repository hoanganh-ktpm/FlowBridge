# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Render APISIX's supported YAML config; no gateway/authentication engine here.
import json
import os
import secrets
import subprocess
from copy import deepcopy
from ipaddress import ip_address, ip_network
from pathlib import Path

import yaml
from container_dev import ROOT, STATE, prepare_state


def render_routes(runtime: dict, cert: str, key: str, api_port: int) -> dict:
    document = yaml.safe_load((ROOT / "infra/gateway/routes.yaml").read_text("utf-8"))
    cors = {
        "allow_origins": ",".join(runtime["cors_origins"]),
        "allow_methods": "GET,OPTIONS",
        "allow_headers": "Authorization,Content-Type,X-Correlation-Id",
        "expose_headers": "X-Correlation-Id",
        "allow_credential": False,
    }
    for route in document["routes"]:
        route["plugins"] = deepcopy(route["plugins"])
        route["plugins"]["cors"] = deepcopy(cors)
        if route["id"] == "identity-me":
            route["plugins"]["openid-connect"].update(
                {
                    "client_id": runtime["oidc_client_id"],
                    "client_secret": runtime["oidc_client_secret"],
                    "discovery": runtime["oidc_issuer"] + "/.well-known/openid-configuration",
                    "introspection_endpoint": runtime["oidc_introspection_url"],
                    "session": {"secret": secrets.token_urlsafe(32)},
                }
            )
    document["upstreams"][0]["nodes"] = {f"identity:{api_port}": 1}
    document["ssls"] = [
        {
            "id": "local-gateway",
            "snis": ["gateway", "gateway-timeout", "localhost"],
            "cert": cert,
            "key": key,
        }
    ]
    # Global rules apply even when authentication or routing rejects the request.
    document["global_rules"] = [
        {
            "id": "request-boundary",
            "plugins": {
                "proxy-rewrite": {
                    "headers": {
                        "remove": [
                            "Forwarded",
                            "X-Forwarded-Host",
                            "X-Real-IP",
                            "X-Tenant-Id",
                            "X-User-Id",
                            "X-Actor-Id",
                            "X-Roles",
                            "X-Permissions",
                            "X-ID-Token",
                            "X-Userinfo",
                            "X-Access-Token",
                            "X-Refresh-Token",
                        ],
                        "set": {
                            "X-Forwarded-For": "$remote_addr",
                            "X-Forwarded-Proto": "$scheme",
                            "X-Correlation-Id": "$flowbridge_correlation_id",
                        },
                    }
                },
                "response-rewrite": {
                    "headers": {"set": {"X-Correlation-Id": "$flowbridge_correlation_id"}}
                },
            },
        }
    ]
    return document


def prepare_gateway(out: Path = Path("/gateway-config")) -> None:
    proxy = ip_address(os.environ["FLOWBRIDGE_GATEWAY_PROXY_IP"])
    subnet = ip_network(os.environ["FLOWBRIDGE_GATEWAY_BACKEND_SUBNET"])
    dynamic = ip_network(os.environ["FLOWBRIDGE_GATEWAY_BACKEND_RANGE"])
    if not dynamic.subnet_of(subnet) or proxy not in subnet or proxy in dynamic:
        raise ValueError("Reserve the gateway IP outside the backend dynamic address range")
    prepare_state(issuer_host="identity")
    runtime_path = STATE / "runtime.json"
    runtime = json.loads(runtime_path.read_text("utf-8"))
    runtime["trusted_proxy_ips"] = [str(proxy)]
    runtime["http_access_log_enabled"] = True
    gateway_runtime = deepcopy(runtime)
    runtime_path.write_text(json.dumps(runtime) + "\n", "utf-8")
    (STATE / "gateway-profile.json").write_text('{"version":1}\n', "utf-8")
    out.mkdir(parents=True, exist_ok=True)
    if not (out / "server.pem").exists():
        if any(out.iterdir()):
            raise ValueError("Incomplete gateway TLS state; inspect before retrying")
        # Standard OpenSSL, separate TLS key from the IAM certificate.
        subprocess.run(
            [
                "openssl",
                "req",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=gateway",
                "-keyout",
                str(out / "server.key"),
                "-out",
                str(out / "server.csr"),
            ],
            check=True,
            capture_output=True,
        )
        (out / "server.ext").write_text(
            "subjectAltName=DNS:gateway,DNS:gateway-timeout,DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\n",
            "ascii",
        )
        subprocess.run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(out / "server.csr"),
                "-CA",
                str(STATE / "certs/ca.pem"),
                "-CAkey",
                str(STATE / "certs/ca.key"),
                "-CAcreateserial",
                "-days",
                "30",
                "-sha256",
                "-extfile",
                str(out / "server.ext"),
                "-out",
                str(out / "server.pem"),
            ],
            check=True,
            capture_output=True,
        )
    document = render_routes(
        gateway_runtime,
        (out / "server.pem").read_text("utf-8"),
        (out / "server.key").read_text("utf-8"),
        int(os.environ["FLOWBRIDGE_DEV_API_PORT"]),
    )
    (out / "apisix.yaml").write_text(yaml.safe_dump(document, sort_keys=False) + "#END\n", "utf-8")
    # An isolated test upstream exercises the real APISIX proxy timeout, not backend behavior.
    timeout_document = deepcopy(document)
    timeout_document["upstreams"][0]["nodes"] = {"slow-upstream:8080": 1}
    (out / "timeout-apisix.yaml").write_text(
        yaml.safe_dump(timeout_document, sort_keys=False) + "#END\n", "utf-8"
    )
    (out / "ca.pem").write_bytes((STATE / "certs/ca.pem").read_bytes())
    for path in out.iterdir():
        os.chown(path, 10001, 0)
        path.chmod(0o644 if path.name == "ca.pem" else 0o640)
    out.chmod(0o750)
    print("Prepared isolated APISIX TLS/OIDC configuration; no database schema changes.")


if __name__ == "__main__":
    try:
        prepare_gateway()
    except Exception as error:
        # Subprocess failures may contain credentials; never print command/output or config.
        print(f"Gateway preparation failed ({type(error).__name__}); inspect private state.")
        raise SystemExit(1) from None
