"""Opt-in real HTTP/Keycloak/Hasura acceptance on pre-approved fixture tables."""

import json
import os
import time
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from tests.e2e.test_automated_onboarding import call, import_manifest, wait_job

from app.core.hasura_provisioner.settings import HasuraSettings

pytestmark = pytest.mark.e2e


def configuration():
    path = os.environ.get("FLOWBRIDGE_RELATIONSHIP_E2E_CONFIG")
    if not path:
        pytest.skip(
            "BLOCKED: approved Core migration, fixture tables and real service config required"
        )
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    assert config["allow_fixture_mutations"] is True
    config["core_url"] = HasuraSettings.validate_endpoint(config["core_url"])
    config["hasura_url"] = HasuraSettings.validate_endpoint(config["hasura_url"])
    config["submissions"] = {
        domain: json.loads(Path(filename).read_text(encoding="utf-8"))
        for domain, filename in config["submissions"].items()
    }
    assert set(config["submissions"]) == {"sales", "warehouse", "procurement"}
    databases = set()
    for submission in config["submissions"].values():
        manifest = submission["manifest"]
        assert manifest["application"]["key"].startswith("rel-e2e-")
        assert len(manifest["sources"]) == 1
        databases.add(manifest["sources"][0]["database"])
        for product in manifest["data_products"]:
            assert product["object"]["schema"].startswith("fb_rel_e2e_")
            assert product["object"]["kind"] == "table"
            assert not product.get("relationships")
    assert len(databases) == 3, "Three distinct pre-existing PostgreSQL databases required"
    for name in ("it_token_env", "consumer_token_env", "warehouse_write_url_env"):
        assert os.environ.get(config[name]), "Missing approved E2E environment binding"
    config["write_url"] = make_url(os.environ[config["warehouse_write_url_env"]])
    warehouse = config["submissions"]["warehouse"]["manifest"]["sources"][0]
    assert (
        config["write_url"].host,
        config["write_url"].port or 5432,
        config["write_url"].database,
    ) == (warehouse["host"], warehouse.get("port", 5432), warehouse["database"])
    UUID(config["consumer_department_id"])
    return config


def publish_submission(client, submission, products, applications):
    receipt = import_manifest(client, submission)
    application_id = receipt["application_id"]
    active = call(
        client,
        "PATCH",
        "/applications/" + application_id,
        json={"expected_revision": receipt["application_revision"], "status": "ACTIVE"},
    )
    applications.append(active)
    for source_id in receipt["data_source_ids"]:
        wait_job(
            client,
            call(
                client,
                "POST",
                "/data-sources/" + source_id + "/connect",
                202,
                json={"expected_revision": 1},
            ),
        )
    for product_id, contract_id in zip(
        receipt["data_product_ids"], receipt["contract_version_ids"], strict=True
    ):
        contract = call(
            client,
            "POST",
            "/data-contracts/" + contract_id + "/publish",
            json={"expected_revision": 1},
        )
        wait_job(
            client,
            call(
                client,
                "POST",
                "/data-products/" + product_id + "/publish",
                202,
                json={"expected_revision": 1, "contract_version_id": contract_id},
            ),
        )
        product = call(client, "GET", "/data-products/" + product_id)
        products[product["object_name"]] = (product, contract)


def grant(client, endpoint, department_id):
    product, contract = endpoint
    scope = {
        "action": "SELECT",
        "row_scope": {"kind": "ALL"},
        "columns": [field["column"] for field in contract["contract_json"]["fields"]],
    }
    request = call(
        client,
        "POST",
        "/access-requests",
        201,
        json={
            "data_product_id": product["id"],
            "contract_version_id": contract["id"],
            "consumer_department_id": department_id,
            "consumer_principal_id": None,
            "requested_scope_json": scope,
            "reason": "Approved relationship E2E fixture",
        },
    )
    accepted = call(
        client,
        "POST",
        "/access-requests/" + request["id"] + "/approve",
        202,
        json={"expected_revision": 1, "scope_json": scope, "expires_at": None},
    )
    wait_job(client, accepted["job"])
    return call(client, "GET", "/access-grants/" + accepted["grant"]["id"])


def test_real_three_database_onboarding_relationship_read_update_revoke(record_property):
    config = configuration()
    products, applications, relationships, grants = {}, [], [], []
    write = sa.create_engine(config["write_url"], hide_parameters=True)
    original_name, table = None, None
    with (
        httpx.Client(
            base_url=config["core_url"],
            timeout=40,
            headers={"Authorization": "Bearer " + os.environ[config["it_token_env"]]},
        ) as it,
        httpx.Client(
            base_url=config["core_url"],
            timeout=40,
            headers={"Authorization": "Bearer " + os.environ[config["consumer_token_env"]]},
        ) as consumer,
        httpx.Client(base_url=config["hasura_url"], timeout=10) as hasura,
    ):
        try:
            version = hasura.get("/v1/version")
            assert version.status_code == 200 and version.json()["version"] == "v2.48.16"
            call(it, "GET", "/me")
            call(consumer, "GET", "/me")
            for submission in config["submissions"].values():
                publish_submission(it, submission, products, applications)
            assert set(products) == {"orders", "order_lines", "products", "suppliers"}
            for left, right, name, kind, mapping in (
                ("orders", "order_lines", "lines", "array", {"id": "order_id"}),
                ("order_lines", "products", "product", "object", {"sku": "sku"}),
                ("products", "suppliers", "supplier", "object", {"supplier_id": "id"}),
            ):
                relationship = call(
                    it,
                    "POST",
                    "/relationships",
                    201,
                    json={
                        "name": name,
                        "source_product_id": products[left][0]["id"],
                        "target_product_id": products[right][0]["id"],
                        "relationship_type": kind,
                        "field_mapping": mapping,
                    },
                )
                relationships.append(relationship["id"])
                wait_job(
                    it,
                    call(
                        it,
                        "POST",
                        "/relationships/" + relationship["id"] + "/publish",
                        202,
                        json={"expected_revision": relationship["revision"]},
                    ),
                )
                applied = call(it, "GET", "/relationships/" + relationship["id"])
                assert (
                    applied["status"] == "ACTIVE"
                    and applied["applied_revision"] == applied["revision"]
                )
            grants.extend(
                grant(it, products[name], config["consumer_department_id"])
                for name in ("orders", "order_lines")
            )
            path = "/data-products/" + products["orders"][0]["id"] + "/query"
            body = {
                "select": ["id"],
                "where": {"id": {"_eq": config["order_id"]}},
                "limit": 1,
                "include": {
                    "lines": {
                        "select": ["id", "sku", "quantity"],
                        "limit": 5,
                        "include": {
                            "product": {
                                "select": ["sku", "name"],
                                "include": {"supplier": {"select": ["name"]}},
                            }
                        },
                    }
                },
            }
            call(consumer, "POST", path, 403, json=body)
            grants.extend(
                grant(it, products[name], config["consumer_department_id"])
                for name in ("products", "suppliers")
            )
            started = time.monotonic()
            rows = call(consumer, "POST", path, json=body)["rows"]
            record_property("nested_read_ms", round((time.monotonic() - started) * 1000, 2))
            assert rows == config["expected_rows"]
            single = {"select": ["id"], "where": body["where"], "limit": 1}
            started = time.monotonic()
            assert call(consumer, "POST", path, json=single)["rows"] == [{"id": config["order_id"]}]
            record_property("single_query_ms", round((time.monotonic() - started) * 1000, 2))
            started = time.monotonic()
            call(
                consumer,
                "GET",
                "/data-products/" + products["orders"][0]["id"] + "/rows",
                params={
                    "contract_version_id": products["orders"][1]["id"],
                    "columns": "id",
                    "limit": 1,
                },
            )
            record_property("legacy_single_rows_ms", round((time.monotonic() - started) * 1000, 2))
            schema = products["products"][0]["schema_name"]
            table = sa.Table(
                "products",
                sa.MetaData(),
                sa.Column("sku", sa.Text),
                sa.Column("name", sa.Text),
                schema=schema,
            )
            with write.begin() as connection:
                original_name = connection.scalar(
                    sa.select(table.c.name).where(table.c.sku == config["product_sku"])
                )
                assert original_name is not None
                updated = connection.execute(
                    table.update()
                    .where(table.c.sku == config["product_sku"])
                    .values(name=original_name + " E2E updated")
                )
                assert updated.rowcount == 1
            changed = call(consumer, "POST", path, json=body)["rows"]
            assert any(
                line["product"] and line["product"]["name"] == original_name + " E2E updated"
                for order in changed
                for line in order["lines"]
            )
            warehouse_grant = next(
                value
                for value in grants
                if value["data_product_id"] == products["products"][0]["id"]
            )
            receipt = call(
                it,
                "POST",
                "/access-grants/" + warehouse_grant["id"] + "/revoke",
                202,
                json={
                    "expected_desired_revision": warehouse_grant["desired_revision"],
                    "reason": "Approved relationship E2E revoke",
                },
            )
            call(consumer, "POST", path, 403, json=body)
            assert call(consumer, "POST", path, json=single)["rows"] == [{"id": config["order_id"]}]
            wait_job(it, receipt)
        finally:
            if original_name is not None:
                with write.begin() as connection:
                    connection.execute(
                        table.update()
                        .where(table.c.sku == config["product_sku"])
                        .values(name=original_name)
                    )
            write.dispose()
            for value in reversed(grants):
                current = call(it, "GET", "/access-grants/" + value["id"])
                if current["status"] not in {"REVOKED", "EXPIRED"}:
                    wait_job(
                        it,
                        call(
                            it,
                            "POST",
                            "/access-grants/" + current["id"] + "/revoke",
                            202,
                            json={
                                "expected_desired_revision": current["desired_revision"],
                                "reason": "Relationship E2E cleanup",
                            },
                        ),
                    )
            for relationship_id in reversed(relationships):
                current = call(it, "GET", "/relationships/" + relationship_id)
                wait_job(
                    it,
                    call(
                        it,
                        "POST",
                        "/relationships/" + relationship_id + "/disable",
                        202,
                        json={
                            "expected_revision": current["revision"],
                            "reason": "Relationship E2E cleanup",
                        },
                    ),
                )
            for product, _ in reversed(list(products.values())):
                current = call(it, "GET", "/data-products/" + product["id"])
                if current["status"] == "ACTIVE":
                    wait_job(
                        it,
                        call(
                            it,
                            "POST",
                            "/data-products/" + current["id"] + "/disable",
                            202,
                            json={
                                "expected_revision": current["revision"],
                                "reason": "Relationship E2E cleanup",
                            },
                        ),
                    )
            for application in reversed(applications):
                call(
                    it,
                    "PATCH",
                    "/applications/" + application["id"],
                    json={"expected_revision": application["revision"], "status": "DISABLED"},
                )
