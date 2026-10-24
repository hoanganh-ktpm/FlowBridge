"""Explicitly approved, additive Workflow test identities; never reactivate old fixtures."""

import json
import secrets
import ssl
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values
from scripts.e2e_environment import ROOT, RUNTIME, engine_for
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.db import models

DIRECTORY = ROOT / ".tmp/flowable-integration"
ISSUER = "https://localhost:18443/realms/flowbridge-it"


def prepare_identities():
    path = DIRECTORY / "test-identities.json"
    if path.exists():
        state = json.loads(path.read_text())
    else:
        run_id = uuid4().hex[:12]
        state = {
            "run_id": run_id,
            "password": secrets.token_urlsafe(36),
            "users": {},
            "principals": {name: str(uuid4()) for name in ("it", "sales", "outsider", "disabled")},
            "departments": {
                name: {"id": str(uuid4()), "code": "fb-wf-" + name + "-" + run_id}
                for name in ("warehouse", "sales")
            },
            "membership_id": str(uuid4()),
            "bootstrapped": False,
        }
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    context = ssl.create_default_context(cafile=str(RUNTIME / "tls/server.crt"))
    previous = json.loads((RUNTIME / "secrets.json").read_text())
    with httpx.Client(verify=context, timeout=25, follow_redirects=False) as http:
        response = http.post(
            "https://localhost:18443/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "flowbridge-test-admin",
                "password": previous["admin_password"],
            },
        )
        response.raise_for_status()
        admin = {"Authorization": "Bearer " + response.json()["access_token"]}
        for label in state["principals"]:
            username = "fb-wf-" + state["run_id"] + "-" + label
            if label in state["users"]:
                endpoint = (
                    "https://localhost:18443/admin/realms/flowbridge-it/users/"
                    + state["users"][label]["id"]
                )
                current = http.get(endpoint, headers=admin)
                current.raise_for_status()
                representation = current.json()
                assert representation["username"] == username
                if not representation.get("email"):
                    representation.update(
                        email=username + "@example.invalid", emailVerified=True, requiredActions=[]
                    )
                    http.put(endpoint, headers=admin, json=representation).raise_for_status()
                continue
            response = http.post(
                "https://localhost:18443/admin/realms/flowbridge-it/users",
                headers=admin,
                json={
                    "username": username,
                    "enabled": True,
                    "emailVerified": True,
                    "email": username + "@example.invalid",
                    "requiredActions": [],
                    "firstName": "Workflow",
                    "lastName": "Acceptance " + label,
                    "credentials": [
                        {"type": "password", "value": state["password"], "temporary": False}
                    ],
                },
            )
            assert response.status_code == 201, "New Workflow test user creation failed"
            identifier = str(UUID(response.headers["Location"].rsplit("/", 1)[1]))
            state["users"][label] = {"id": identifier, "username": username}
            path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if not state["bootstrapped"]:
        engine = engine_for(make_url(dotenv_values(ROOT / ".env")["DXOS_CORE_URL"]))
        try:
            with engine.begin() as connection:
                for label, identifier in state["principals"].items():
                    assert not connection.scalar(
                        select(models.principals.c.id).where(
                            models.principals.c.id == UUID(identifier)
                        )
                    )
                    connection.execute(
                        models.principals.insert().values(
                            id=UUID(identifier),
                            issuer=ISSUER,
                            subject=state["users"][label]["id"],
                            kind="human",
                            display_name="Workflow test " + label + " " + state["run_id"],
                            platform_role="PLATFORM_ADMIN" if label == "it" else "NONE",
                            status="DISABLED" if label == "disabled" else "ACTIVE",
                        )
                    )
                for name, department in state["departments"].items():
                    connection.execute(
                        models.departments.insert().values(
                            id=UUID(department["id"]),
                            code=department["code"],
                            name="Workflow test " + name + " " + state["run_id"],
                            status="ACTIVE",
                        )
                    )
                connection.execute(
                    models.department_memberships.insert().values(
                        id=UUID(state["membership_id"]),
                        principal_id=UUID(state["principals"]["sales"]),
                        department_id=UUID(state["departments"]["sales"]["id"]),
                        membership_role="STAFF",
                        status="ACTIVE",
                    )
                )
            state["bootstrapped"] = True
            path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        finally:
            engine.dispose()
    return state


def workflow_tokens(state):
    context = ssl.create_default_context(cafile=str(RUNTIME / "tls/server.crt"))
    result = {}
    with httpx.Client(verify=context, timeout=25, follow_redirects=False) as http:
        for label, user in state["users"].items():
            response = http.post(
                ISSUER + "/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "flowbridge-e2e",
                    "username": user["username"],
                    "password": state["password"],
                    "scope": "openid",
                },
            )
            response.raise_for_status()
            result[label] = response.json()["access_token"]
    return result
