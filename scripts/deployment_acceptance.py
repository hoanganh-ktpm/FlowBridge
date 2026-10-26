"""Real Layer 1 acceptance against the generated single-environment deployment."""

import argparse
import base64
import json
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa
import yaml
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL

from app.core.db import models
from scripts.database.products import draft_submissions
from scripts.database.seed import remove_demo_rows, seed_databases, verify_demo_rows
from scripts.deployment_config import _env_file

PREFIX = "/api/v1/core"
CLIENTS = ("flowbridge-e2e-it", "flowbridge-e2e-consumer", "flowbridge-e2e-outsider")
BUSINESS_DATABASES = ("sales", "warehouse", "logistics")


def _generated_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line
    )


def _call(client, method, path, expected=200, **kwargs):
    if method not in {"GET", "HEAD"}:
        kwargs.setdefault("headers", {"Idempotency-Key": uuid4().hex})
    response = client.request(method, PREFIX + path, **kwargs)
    if response.status_code != expected:
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
    return response.json()


def _wait_job(client, job_id):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        response = client.get(PREFIX + "/publication-jobs/" + str(job_id))
        if response.status_code == 200:
            job = response.json()
            if job["status"] == "SUCCEEDED":
                return
            if job["status"] in {"FAILED", "CANCELLED"}:
                raise RuntimeError("publication job failed: " + str(job.get("last_error_code")))
        time.sleep(0.5)
    raise RuntimeError("publication job timed out")


def _subject(token: str) -> str:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["sub"]


def _expired_token(issuer: str, audience: str) -> str:
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return (
        encode({"alg": "HS256", "typ": "JWT"})
        + "."
        + encode({"iss": issuer, "aud": audience, "exp": 1, "sub": "expired-acceptance"})
        + ".invalid"
    )


def _run_acceptance(config_path: Path) -> dict[str, str]:
    root = config_path.resolve().parent
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    secrets = _env_file(root / "secrets" / "deployment.env")
    stack = root / "infra" / "deployment" / "generated"
    acceptance = _generated_env(stack / "acceptance.env")
    core_values = _generated_env(stack / "core.env")
    issuer = config["identity"]["issuer"]
    token_url = issuer + "/protocol/openid-connect/token"
    tokens = {}
    with httpx.Client(timeout=20, follow_redirects=False) as http:
        for client_id in CLIENTS:
            response = http.post(
                token_url,
                data={"grant_type": "client_credentials"},
                auth=(client_id, acceptance[client_id]),
            )
            response.raise_for_status()
            tokens[client_id] = response.json()["access_token"]

    names = config["database"]["names"]
    passwords = config["database"]["role_password_env"]
    port = config["services"]["postgres"]["port"]

    def owner_url(domain, driver="psycopg"):
        return URL.create(
            "postgresql+" + driver,
            username=f"{domain}_owner",
            password=secrets[passwords[domain]],
            host="127.0.0.1",
            port=port,
            database=names[domain],
            query={"sslmode": "require"},
        )

    core = sa.create_engine(owner_url("core"), hide_parameters=True)
    principal_ids = {}
    department_ids = {}
    with core.begin() as connection:
        for client_id in CLIENTS:
            identifier = uuid4()
            principal_ids[client_id] = identifier
            connection.execute(
                insert(models.principals)
                .values(
                    id=identifier,
                    issuer=issuer,
                    subject=_subject(tokens[client_id]),
                    kind="service",
                    service_client_id=client_id,
                    display_name="Layer 1 acceptance " + client_id,
                    platform_role="PLATFORM_ADMIN" if client_id.endswith("-it") else "NONE",
                    status="ACTIVE",
                )
                .on_conflict_do_update(
                    index_elements=[models.principals.c.issuer, models.principals.c.subject],
                    set_={
                        "status": "ACTIVE",
                        "platform_role": "PLATFORM_ADMIN" if client_id.endswith("-it") else "NONE",
                    },
                )
            )
            principal_ids[client_id] = connection.scalar(
                sa.select(models.principals.c.id).where(
                    models.principals.c.issuer == issuer,
                    models.principals.c.subject == _subject(tokens[client_id]),
                )
            )
        for domain in ("sales", "warehouse", "logistics"):
            identifier = uuid4()
            connection.execute(
                insert(models.departments)
                .values(id=identifier, code=domain, name=domain.title(), status="ACTIVE")
                .on_conflict_do_update(
                    index_elements=[models.departments.c.code], set_={"status": "ACTIVE"}
                )
            )
            department_ids[domain] = connection.scalar(
                sa.select(models.departments.c.id).where(models.departments.c.code == domain)
            )
        connection.execute(
            insert(models.department_memberships)
            .values(
                id=uuid4(),
                principal_id=principal_ids["flowbridge-e2e-consumer"],
                department_id=department_ids["sales"],
                membership_role="STAFF",
                status="ACTIVE",
            )
            .on_conflict_do_update(
                index_elements=[
                    models.department_memberships.c.principal_id,
                    models.department_memberships.c.department_id,
                ],
                set_={"status": "ACTIVE"},
            )
        )

    clients = {
        name: httpx.Client(
            base_url=config["services"]["core"]["public_url"],
            headers={"Authorization": "Bearer " + token},
            timeout=30,
        )
        for name, token in tokens.items()
    }
    it = clients["flowbridge-e2e-it"]
    consumer = clients["flowbridge-e2e-consumer"]
    outsider = clients["flowbridge-e2e-outsider"]
    grant_id = None
    try:
        assert _call(it, "GET", "/me")["platform_role"] == "PLATFORM_ADMIN"
        assert (
            httpx.get(
                config["services"]["core"]["public_url"] + PREFIX + "/me", timeout=20
            ).status_code
            == 401
        )
        assert (
            it.get(PREFIX + "/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
        )
        assert (
            it.get(
                PREFIX + "/me",
                headers={
                    "Authorization": "Bearer "
                    + _expired_token(issuer, config["identity"]["client_id"])
                },
            ).status_code
            == 401
        )
        for domain in ("sales", "warehouse", "logistics"):
            os.environ[f"DXOS_{domain.upper()}_URL"] = owner_url(domain).render_as_string(
                hide_password=False
            )
        submissions = draft_submissions()
        stock = None
        for domain, submission in submissions.items():
            source = submission["manifest"]["sources"][0]
            source.update(
                {
                    "host": "postgres",
                    "port": 5432,
                    "database": names[domain],
                    "username": f"{domain}_reader",
                    "ssl_mode": "require",
                    "secret_ref": f"{domain}-reader",
                }
            )
            with core.connect() as connection:
                existing = connection.scalar(
                    sa.select(models.applications.c.id).where(
                        models.applications.c.app_key == domain
                    )
                )
                if existing:
                    source_ids = connection.scalars(
                        sa.select(models.data_sources.c.id).where(
                            models.data_sources.c.application_id == existing
                        )
                    ).all()
                    product_ids = connection.scalars(
                        sa.select(models.data_products.c.id).where(
                            models.data_products.c.data_source_id.in_(source_ids)
                        )
                    ).all()
            if existing:
                imported = {
                    "application_id": str(existing),
                    "data_source_ids": [str(value) for value in source_ids],
                    "data_product_ids": [str(value) for value in product_ids],
                }
            else:
                assert _call(it, "POST", "/application-onboarding/validate", json=submission)[
                    "valid"
                ]
                plan = _call(
                    it,
                    "POST",
                    "/application-onboarding/plans",
                    json={"submission": submission, "discovery_mode": "LIVE"},
                )
                if not plan["valid"] or plan["evidence"] != "LIVE_VERIFIED":
                    raise RuntimeError(domain + " live onboarding plan failed")
                imported = _call(
                    it,
                    "POST",
                    "/application-onboarding/imports",
                    201,
                    json={
                        "submission": submission,
                        "plan_hash": plan["plan_hash"],
                        "expected_application_revision": 0,
                    },
                )
            app_id = UUID(imported["application_id"])
            with core.connect() as connection:
                app = connection.execute(
                    sa.select(models.applications.c.revision, models.applications.c.status).where(
                        models.applications.c.id == app_id
                    )
                ).one()
            if app.status == "DRAFT":
                _call(
                    it,
                    "PATCH",
                    "/applications/" + str(app_id),
                    json={"expected_revision": app.revision, "status": "ACTIVE"},
                )
            source_id = UUID(imported["data_source_ids"][0])
            deadline = time.monotonic() + 120
            while True:
                current = _call(it, "GET", "/data-sources/" + str(source_id))
                if current["status"] == "CONNECTED":
                    break
                if current["status"] in {"DRAFT", "FAILED"}:
                    receipt = _call(
                        it,
                        "POST",
                        "/data-sources/" + str(source_id) + "/connect",
                        202,
                        json={"expected_revision": current["revision"]},
                    )
                    _wait_job(it, receipt["job_id"])
                    continue
                if time.monotonic() > deadline:
                    raise RuntimeError(domain + " source connection failed")
                time.sleep(0.5)
            _call(
                it,
                "GET",
                "/data-sources/" + str(source_id) + "/schema",
                params={"schema_name": "public"},
            )
            for product_id_text in imported["data_product_ids"]:
                product_id = UUID(product_id_text)
                product = _call(it, "GET", "/data-products/" + product_id_text)
                with core.connect() as connection:
                    contract_id = connection.scalar(
                        sa.select(models.data_contract_versions.c.id).where(
                            models.data_contract_versions.c.data_product_id == product_id
                        )
                    )
                contract = _call(it, "GET", "/data-contracts/" + str(contract_id))
                proposal = next(
                    value["contract_json"]
                    for value in submission["product_contracts"]
                    if value["product_key"] == product["object_name"].removeprefix("v_")
                )
                if contract["contract_json"] != proposal:
                    contract = _call(
                        it,
                        "PATCH",
                        "/data-contracts/" + str(contract_id),
                        json={"expected_revision": contract["revision"], "contract_json": proposal},
                    )
                if contract["status"] == "DRAFT":
                    _call(
                        it,
                        "POST",
                        "/data-contracts/" + str(contract_id) + "/publish",
                        json={"expected_revision": contract["revision"]},
                    )
                if product["status"] != "ACTIVE":
                    receipt = _call(
                        it,
                        "POST",
                        "/data-products/" + product_id_text + "/publish",
                        202,
                        json={
                            "expected_revision": product["revision"],
                            "contract_version_id": str(contract_id),
                        },
                    )
                    _wait_job(it, receipt["job_id"])
                if domain == "warehouse" and product["object_name"] == "v_stock_availability":
                    stock = (product_id_text, str(contract_id))
        if stock is None:
            raise RuntimeError("warehouse stock Data Product missing")

        product_id, contract_id = stock
        with core.connect() as connection:
            stale_grants = connection.scalars(
                sa.select(models.access_grants.c.id).where(
                    models.access_grants.c.data_product_id == UUID(product_id),
                    models.access_grants.c.consumer_department_id == department_ids["sales"],
                    models.access_grants.c.status.not_in(("REVOKED", "EXPIRED")),
                )
            ).all()
        for stale_id in stale_grants:
            stale = _call(it, "GET", "/access-grants/" + str(stale_id))
            if stale["status"] != "REVOKING":
                receipt = _call(
                    it,
                    "POST",
                    "/access-grants/" + str(stale_id) + "/revoke",
                    202,
                    json={
                        "expected_desired_revision": stale["desired_revision"],
                        "reason": "Layer 1 acceptance stale-run cleanup",
                    },
                )
                _wait_job(it, receipt["job_id"])
            else:
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    stale = _call(it, "GET", "/access-grants/" + str(stale_id))
                    if stale["status"] in {"REVOKED", "EXPIRED"}:
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError("stale acceptance grant cleanup timed out")
        columns = ["org_id", "warehouse_id", "product_id", "sku", "available_quantity"]
        scope = {"action": "SELECT", "columns": columns, "row_scope": {"kind": "ALL"}}
        request = _call(
            it,
            "POST",
            "/access-requests",
            201,
            json={
                "data_product_id": product_id,
                "contract_version_id": contract_id,
                "consumer_department_id": str(department_ids["sales"]),
                "consumer_principal_id": None,
                "requested_scope_json": scope,
                "reason": "Layer 1 deployment acceptance",
            },
        )
        approved = _call(
            it,
            "POST",
            "/access-requests/" + request["id"] + "/approve",
            202,
            json={
                "expected_revision": request["revision"],
                "scope_json": scope,
                "expires_at": None,
            },
        )
        grant_id = approved["grant"]["id"]
        _wait_job(it, approved["job"]["job_id"])
        path = "/data-products/" + product_id + "/rows"
        params = {"contract_version_id": contract_id, "columns": ",".join(columns), "limit": 100}
        rows = _call(consumer, "GET", path, params=params)["rows"]
        if not rows:
            raise RuntimeError("governed Hasura read returned no real rows")
        assert outsider.get(PREFIX + path, params=params).status_code == 403
        assert (
            outsider.get(
                PREFIX + path, params=params, headers={"X-Hasura-Role": "admin"}
            ).status_code
            == 403
        )
        grant = _call(it, "GET", "/access-grants/" + approved["grant"]["id"])
        revoked = _call(
            it,
            "POST",
            "/access-grants/" + grant["id"] + "/revoke",
            202,
            json={
                "expected_desired_revision": grant["desired_revision"],
                "reason": "Layer 1 acceptance completed",
            },
        )
        _wait_job(it, revoked["job_id"])
        grant_id = None
        assert consumer.get(PREFIX + path, params=params).status_code == 403

        metadata_headers = {"X-Hasura-Admin-Secret": core_values["FLOWBRIDGE_HASURA_ADMIN_SECRET"]}
        with httpx.Client(
            base_url=config["services"]["hasura"]["public_url"], timeout=20
        ) as hasura:
            metadata = hasura.post(
                "/v1/metadata",
                headers=metadata_headers,
                json={"type": "export_metadata", "args": {}},
            ).json()
            expected = {
                submission["manifest"]["application"]["key"] for submission in submissions.values()
            }
            actual = {source["name"].split("_pg_")[0] for source in metadata["sources"]}
            if not expected <= actual:
                raise RuntimeError("Hasura is missing a business source")
            inconsistent = hasura.post(
                "/v1/metadata",
                headers=metadata_headers,
                json={"type": "get_inconsistent_metadata", "args": {}},
            ).json()
            if inconsistent.get("is_consistent") is not True:
                raise RuntimeError("Hasura metadata is inconsistent")
            warehouse_source = next(
                source["name"]
                for source in metadata["sources"]
                if source["name"].startswith("warehouse_pg_")
            )
            query = {"query": "{" + warehouse_source + "{v_stock_availability{sku}}}"}
            if "errors" not in hasura.post("/v1/graphql", json=query).json():
                raise RuntimeError("Hasura accepted an unauthenticated business query")
            if (
                "errors"
                not in hasura.post(
                    "/v1/graphql", json=query, headers={"X-Hasura-Role": "admin"}
                ).json()
            ):
                raise RuntimeError("Hasura trusted a caller-supplied role header")
        return {
            "keycloak_core": "PASS",
            "no_token": "PASS",
            "invalid_token": "PASS",
            "expired_token": "PASS",
            "hasura_three_sources": "PASS",
            "grant_read_revoke": "PASS",
            "header_escalation": "PASS",
            "acceptance_identities_disabled": "PASS",
        }
    finally:
        if grant_id is not None:
            try:
                grant = _call(it, "GET", "/access-grants/" + grant_id)
                if grant["status"] not in {"REVOKED", "EXPIRED", "REVOKING"}:
                    receipt = _call(
                        it,
                        "POST",
                        "/access-grants/" + grant_id + "/revoke",
                        202,
                        json={
                            "expected_desired_revision": grant["desired_revision"],
                            "reason": "Layer 1 acceptance failure cleanup",
                        },
                    )
                    _wait_job(it, receipt["job_id"])
            except Exception:
                pass
        with core.begin() as connection:
            connection.execute(
                models.department_memberships.update()
                .where(models.department_memberships.c.principal_id.in_(principal_ids.values()))
                .values(status="REVOKED")
            )
            connection.execute(
                models.principals.update()
                .where(models.principals.c.id.in_(principal_ids.values()))
                .values(status="DISABLED")
            )
        for client in clients.values():
            client.close()
        core.dispose()


def _configure_fixture_environment(config_path: Path) -> None:
    root = config_path.resolve().parent
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    secrets = _env_file(root / "secrets" / "deployment.env")
    for domain in BUSINESS_DATABASES:
        os.environ[f"DXOS_{domain.upper()}_URL"] = URL.create(
            "postgresql+psycopg",
            username=f"{domain}_owner",
            password=secrets[config["database"]["role_password_env"][domain]],
            host="127.0.0.1",
            port=config["services"]["postgres"]["port"],
            database=config["database"]["names"][domain],
            query={"sslmode": "require"},
        ).render_as_string(hide_password=False)


def run(config_path: Path) -> dict[str, str]:
    _configure_fixture_environment(config_path)
    try:
        seed_databases(BUSINESS_DATABASES)
        verify_demo_rows(BUSINESS_DATABASES)
        return _run_acceptance(config_path)
    finally:
        remove_demo_rows(BUSINESS_DATABASES)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("deployment.yaml"))
    args = parser.parse_args(argv)
    try:
        result = run(args.config)
    except Exception as error:
        detail = str(error) if isinstance(error, (RuntimeError, AssertionError)) else ""
        print("FAIL: " + type(error).__name__ + (": " + detail if detail else ""))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
