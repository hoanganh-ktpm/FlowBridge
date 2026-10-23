"""Opt-in real HTTP/SSO/Hasura acceptance; no dependency overrides or mocks."""

import json
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.core.db import models
from app.core.hasura_provisioner.schemas import department_role
from app.core.hasura_provisioner.settings import HasuraSettings

pytestmark = pytest.mark.e2e
PREFIX = "/api/v1/core"


def configuration():
    if os.environ.get("FLOWBRIDGE_E2E_ENABLED") != "1":
        pytest.skip("BLOCKED: enable only against explicitly provisioned isolated real services")
    names = (
        "CORE_URL",
        "CORE_DATABASE_URL",
        "OWNER_WRITE_URL",
        "OWNER_SUBMISSION",
        "CONSUMER_SUBMISSION",
        "IT_TOKEN",
        "CONSUMER_TOKEN",
        "OUTSIDER_TOKEN",
        "HASURA_ENDPOINT",
        "HASURA_ADMIN_SECRET",
    )
    missing = [name for name in names if not os.environ.get("FLOWBRIDGE_E2E_" + name)]
    assert not missing, "Missing E2E configuration: " + ", ".join(missing)
    config = {name: os.environ["FLOWBRIDGE_E2E_" + name] for name in names}
    for name in ("CORE_URL", "HASURA_ENDPOINT"):
        config[name] = HasuraSettings.validate_endpoint(config[name])
    for name in ("CORE_DATABASE_URL", "OWNER_WRITE_URL"):
        url = make_url(config[name])
        assert url.drivername == "postgresql+psycopg"
        assert url.database.startswith("flowbridge_e2e_"), "Dedicated E2E databases required"
    for name in ("OWNER_SUBMISSION", "CONSUMER_SUBMISSION"):
        config[name] = json.loads(Path(config[name]).read_text(encoding="utf-8"))
    sources = [
        config[name]["manifest"]["sources"] for name in ("OWNER_SUBMISSION", "CONSUMER_SUBMISSION")
    ]
    assert all(len(items) == 1 for items in sources)
    assert all(items[0]["database"].startswith("flowbridge_e2e_") for items in sources)
    assert (
        len(
            {items[0]["database"] for items in sources}
            | {make_url(config["CORE_DATABASE_URL"]).database}
        )
        == 3
    )
    owner = sources[0][0]
    write = make_url(config["OWNER_WRITE_URL"])
    assert (write.host, write.port or 5432, write.database) == (
        owner["host"],
        owner.get("port", 5432),
        owner["database"],
    )
    product = config["OWNER_SUBMISSION"]["manifest"]["data_products"]
    assert len(product) == 1
    assert product[0]["object"] == {"schema": "public", "name": "stock_available", "kind": "table"}
    contract = config["OWNER_SUBMISSION"]["product_contracts"][0]["contract_json"]
    assert [(field["column"], field["name"]) for field in contract["fields"]] == [
        ("sku", "sku"),
        ("available_quantity", "available_quantity"),
    ]
    return config


def call(client, method, path, expected=200, **kwargs):
    if method not in {"GET", "HEAD"}:
        kwargs.setdefault("headers", {"Idempotency-Key": uuid4().hex})
    response = client.request(method, PREFIX + path, **kwargs)
    assert response.status_code == expected, f"{method} {path}: HTTP {response.status_code}"
    return response.json()


def wait_job(client, receipt):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        response = client.get(PREFIX + "/publication-jobs/" + receipt["job_id"])
        if response.status_code == 409:
            time.sleep(0.5)
            continue
        assert response.status_code == 200, "Job status HTTP " + str(response.status_code)
        job = response.json()
        if job["status"] == "SUCCEEDED":
            return
        assert job["status"] not in {"FAILED", "CANCELLED"}, "Publication failed: " + str(
            job.get("last_error_code")
        )
        time.sleep(0.5)
    pytest.fail("Publication timeout: verify the separately running real worker")


def assert_binding(connection, context):
    row = (
        connection.execute(
            sa.select(models.principals).where(
                models.principals.c.id == UUID(context["principal_id"])
            )
        )
        .mappings()
        .one()
    )
    assert row["issuer"] == context["verified_identity"]["issuer"]
    assert row["subject"] == context["verified_identity"]["subject"]
    assert row["platform_role"] == context["platform_role"]


def import_manifest(client, submission):
    assert call(client, "POST", "/application-onboarding/validate", json=submission)["valid"]
    plan = call(
        client,
        "POST",
        "/application-onboarding/plans",
        json={"submission": submission, "discovery_mode": "LIVE"},
    )
    assert plan["valid"] and plan["evidence"] == "LIVE_VERIFIED"
    payload = {
        "submission": submission,
        "plan_hash": plan["plan_hash"],
        "expected_application_revision": 0,
    }
    headers = {"Idempotency-Key": uuid4().hex}
    receipt = call(
        client, "POST", "/application-onboarding/imports", 201, json=payload, headers=headers
    )
    assert (
        call(client, "POST", "/application-onboarding/imports", 201, json=payload, headers=headers)
        == receipt
    )
    return receipt


def permission(metadata, source_key, role, table_name="stock_available"):
    source = next(item for item in metadata["sources"] if item["name"] == source_key)
    table = next(
        item
        for item in source["tables"]
        if item["table"] == {"schema": "public", "name": table_name}
    )
    return next(
        (
            item["permission"]
            for item in table.get("select_permissions", [])
            if item["role"] == role
        ),
        None,
    )


def test_real_manifest_publication_read_update_revoke():
    config = configuration()
    core = sa.create_engine(config["CORE_DATABASE_URL"], hide_parameters=True)
    owner = sa.create_engine(config["OWNER_WRITE_URL"], hide_parameters=True)
    clients = {
        name: httpx.Client(
            base_url=config["CORE_URL"],
            headers={"Authorization": "Bearer " + config[name + "_TOKEN"]},
            timeout=30,
            follow_redirects=False,
        )
        for name in ("IT", "CONSUMER", "OUTSIDER")
    }
    metadata_client = httpx.Client(timeout=30, follow_redirects=False)
    original_quantity = None
    grant = None
    try:
        contexts = {name: call(client, "GET", "/me") for name, client in clients.items()}
        assert len({context["principal_id"] for context in contexts.values()}) == 3
        assert contexts["IT"]["platform_role"] == "PLATFORM_ADMIN"
        assert all(contexts[name]["platform_role"] == "NONE" for name in ("CONSUMER", "OUTSIDER"))
        with core.connect() as connection:
            for context in contexts.values():
                assert_binding(connection, context)
            department = (
                connection.execute(
                    sa.select(models.departments).where(
                        models.departments.c.code
                        == config["CONSUMER_SUBMISSION"]["manifest"]["application"][
                            "department_key"
                        ]
                    )
                )
                .mappings()
                .one()
            )
        department_id = str(department["id"])
        assert any(
            item["department_id"] == department_id and item["status"] == "ACTIVE"
            for item in contexts["CONSUMER"]["memberships"]
        )
        assert all(
            item["department_id"] != department_id for item in contexts["OUTSIDER"]["memberships"]
        )
        with owner.connect() as connection:
            rows = connection.execute(
                sa.text("SELECT sku, available_quantity FROM public.stock_available")
            ).all()
        assert len(rows) == 1 and rows[0].sku == "SYNTHETIC-SKU"
        original_quantity = rows[0].available_quantity

        it = clients["IT"]
        call(it, "POST", "/application-onboarding/validate", 422, json={"manifest": {}})
        imported = import_manifest(it, config["OWNER_SUBMISSION"])
        import_manifest(it, config["CONSUMER_SUBMISSION"])
        application_id = imported["application_id"]
        source_id = imported["data_source_ids"][0]
        product_id = imported["data_product_ids"][0]
        contract_id = imported["contract_version_ids"][0]
        application = call(it, "GET", "/applications/" + application_id)
        call(
            it,
            "PATCH",
            "/applications/" + application_id,
            json={"expected_revision": application["revision"], "status": "ACTIVE"},
        )
        source = call(it, "GET", "/data-sources/" + source_id)
        wait_job(
            it,
            call(
                it,
                "POST",
                "/data-sources/" + source_id + "/connect",
                202,
                json={"expected_revision": source["revision"]},
            ),
        )
        contract = call(it, "GET", "/data-contracts/" + contract_id)
        call(
            it,
            "POST",
            "/data-contracts/" + contract_id + "/publish",
            json={"expected_revision": contract["revision"]},
        )
        product = call(it, "GET", "/data-products/" + product_id)
        wait_job(
            it,
            call(
                it,
                "POST",
                "/data-products/" + product_id + "/publish",
                202,
                json={"expected_revision": product["revision"], "contract_version_id": contract_id},
            ),
        )
        assert call(it, "GET", "/data-products/" + product_id)["status"] == "ACTIVE"
        path = "/data-products/" + product_id + "/rows"
        params = {"contract_version_id": contract_id, "columns": "sku,available_quantity"}
        call(clients["CONSUMER"], "GET", path, 403, params=params)
        scope = {
            "action": "SELECT",
            "columns": ["sku", "available_quantity"],
            "row_scope": {"kind": "ALL"},
        }
        requested = call(
            it,
            "POST",
            "/access-requests",
            201,
            json={
                "data_product_id": product_id,
                "contract_version_id": contract_id,
                "consumer_department_id": department_id,
                "consumer_principal_id": None,
                "requested_scope_json": scope,
                "reason": "Isolated E2E stock lookup",
            },
        )
        approved = call(
            it,
            "POST",
            "/access-requests/" + requested["id"] + "/approve",
            202,
            json={
                "expected_revision": requested["revision"],
                "scope_json": scope,
                "expires_at": None,
            },
        )
        grant = approved["grant"]
        wait_job(it, approved["job"])

        def export():
            response = metadata_client.post(
                config["HASURA_ENDPOINT"].rstrip("/") + "/v1/metadata",
                headers={"x-hasura-admin-secret": config["HASURA_ADMIN_SECRET"]},
                json={"type": "export_metadata", "version": 2, "args": {}},
            )
            assert response.status_code == 200
            return response.json()["metadata"]

        role = department_role(UUID(department_id))
        assert set(permission(export(), source["hasura_source_name"], role)["columns"]) == set(
            scope["columns"]
        )
        data = call(clients["CONSUMER"], "GET", path, params=params)
        assert data["rows"] == [{"sku": "SYNTHETIC-SKU", "available_quantity": original_quantity}]
        call(clients["OUTSIDER"], "GET", path, 403, params=params)
        call(clients["CONSUMER"], "GET", path, 422, params={**params, "columns": "sku,unit_cost"})
        with owner.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE public.stock_available SET available_quantity = :quantity "
                    "WHERE sku = :sku"
                ),
                {"quantity": original_quantity + 1, "sku": "SYNTHETIC-SKU"},
            )
        assert (
            call(clients["CONSUMER"], "GET", path, params=params)["rows"][0]["available_quantity"]
            == original_quantity + 1
        )
        revoked = call(
            it,
            "POST",
            "/access-grants/" + grant["id"] + "/revoke",
            202,
            json={
                "expected_desired_revision": grant["desired_revision"],
                "reason": "Isolated E2E complete",
            },
        )
        grant = None
        call(clients["CONSUMER"], "GET", path, 403, params=params)
        wait_job(it, revoked)
        assert permission(export(), source["hasura_source_name"], role) is None
        call(clients["CONSUMER"], "GET", path, 403, params=params)
    finally:
        try:
            if grant is not None:
                call(
                    clients["IT"],
                    "POST",
                    "/access-grants/" + grant["id"] + "/revoke",
                    202,
                    json={
                        "expected_desired_revision": grant["desired_revision"],
                        "reason": "Failed E2E cleanup",
                    },
                )
            if original_quantity is not None:
                with owner.begin() as connection:
                    connection.execute(
                        sa.text(
                            "UPDATE public.stock_available SET available_quantity = :quantity "
                            "WHERE sku = :sku"
                        ),
                        {"quantity": original_quantity, "sku": "SYNTHETIC-SKU"},
                    )
        finally:
            for client in clients.values():
                client.close()
            metadata_client.close()
            core.dispose()
            owner.dispose()
