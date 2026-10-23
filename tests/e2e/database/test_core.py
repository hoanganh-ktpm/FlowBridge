from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from scripts.database.registry import metadata_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


@pytest.fixture
def core_records(databases):
    tables = {table.name: table for table in metadata_for("core").tables.values()}
    with databases["core"].connect() as connection:
        transaction = connection.begin()
        principal_id, product_id, contract_id, request_id, grant_id = [uuid4() for _ in range(5)]
        department_id = connection.scalar(select(tables["departments"].c.id).limit(1))
        source_id = connection.scalar(select(tables["data_sources"].c.id).limit(1))
        application_id = connection.scalar(select(tables["applications"].c.id).limit(1))
        scope = {"action": "SELECT", "columns": ["sku"], "row_scope": {"kind": "ALL"}}
        now = datetime.now(UTC)
        try:
            connection.execute(
                tables["principals"]
                .insert()
                .values(
                    id=principal_id,
                    issuer="https://identity.example",
                    subject=str(principal_id),
                    kind="human",
                    platform_role="NONE",
                )
            )
            connection.execute(
                tables["data_products"]
                .insert()
                .values(
                    id=product_id,
                    data_source_id=source_id,
                    product_key="test.inventory",
                    name="Test inventory",
                    schema_name="public",
                    object_name="test_inventory",
                    object_type="TABLE",
                )
            )
            connection.execute(
                tables["data_contract_versions"]
                .insert()
                .values(
                    id=contract_id,
                    data_product_id=product_id,
                    version="1.0.0",
                    contract_json={},
                )
            )
            connection.execute(
                tables["access_requests"]
                .insert()
                .values(
                    id=request_id,
                    data_product_id=product_id,
                    contract_version_id=contract_id,
                    requested_by=principal_id,
                    consumer_department_id=department_id,
                    requested_scope_json=scope,
                    reason="Integration fixture",
                    status="APPROVED",
                    decided_by=principal_id,
                    decided_at=now,
                )
            )
            connection.execute(
                tables["access_grants"]
                .insert()
                .values(
                    id=grant_id,
                    data_product_id=product_id,
                    contract_version_id=contract_id,
                    access_request_id=request_id,
                    consumer_department_id=department_id,
                    scope_json=scope,
                    enforcement_role="test_department",
                    granted_by=principal_id,
                )
            )
            yield (
                connection,
                tables,
                {
                    "principal": principal_id,
                    "product": product_id,
                    "contract": contract_id,
                    "request": request_id,
                    "grant": grant_id,
                    "application": application_id,
                    "scope": scope,
                    "now": now,
                },
            )
        finally:
            transaction.rollback()


def reject(connection, statement, code="23514"):
    with pytest.raises(IntegrityError) as error:
        with connection.begin_nested():
            connection.execute(statement)
    assert error.value.orig.sqlstate == code


def test_human_cannot_be_bound_as_service(core_records):
    connection, tables, values = core_records
    reject(connection, tables["principals"].update().values(application_id=values["application"]))


def test_grant_scope_is_select_only(core_records):
    connection, tables, values = core_records
    reject(
        connection,
        tables["access_grants"].update().values(scope_json={**values["scope"], "action": "DELETE"}),
    )


def test_active_grant_requires_applied_revision(core_records):
    connection, tables, values = core_records
    reject(connection, tables["access_grants"].update().values(status="ACTIVE"))


def test_contract_publish_requires_actor_and_time(core_records):
    connection, tables, values = core_records
    reject(connection, tables["data_contract_versions"].update().values(status="PUBLISHED"))


def test_contract_cannot_reference_wrong_product(core_records):
    connection, tables, values = core_records
    other = uuid4()
    source_id = connection.scalar(select(tables["data_sources"].c.id).limit(1))
    connection.execute(
        tables["data_products"]
        .insert()
        .values(
            id=other,
            data_source_id=source_id,
            product_key="test.other",
            name="Other",
            schema_name="public",
            object_name="other",
            object_type="TABLE",
        )
    )
    reject(connection, tables["access_grants"].update().values(data_product_id=other), "23503")


def test_jobs_require_matching_target_type(core_records):
    connection, tables, values = core_records
    reject(
        connection,
        tables["publication_jobs"]
        .insert()
        .values(
            job_type="CONNECT_SOURCE",
            data_product_id=values["product"],
            target_revision=1,
            idempotency_key=str(uuid4()),
            correlation_id="test-core",
        ),
    )


def test_receipts_require_at_least_seven_days_retention(core_records):
    connection, tables, values = core_records
    reject(
        connection,
        tables["core_command_receipts"]
        .insert()
        .values(
            namespace="test",
            fingerprint="a" * 64,
            status=201,
            body={},
            created_at=values["now"],
            expires_at=values["now"] + timedelta(days=1),
        ),
    )
