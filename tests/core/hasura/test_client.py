import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.core.hasura_provisioner import HasuraMetadataClient, HasuraSettings
from app.core.hasura_provisioner.errors import (
    HasuraRejected,
    HasuraTimeout,
    HasuraUnavailable,
    InvalidConfiguration,
    InvalidMetadata,
    MetadataConflict,
)
from app.core.hasura_provisioner.schemas import SelectPermission

from .fakes import DEPARTMENT_ID

pytestmark = pytest.mark.asyncio


async def provision(harness):
    connection = await harness.secrets.resolve_postgres("warehouse-reader")
    await harness.client.add_postgres_source("warehouse", connection)
    await harness.client.track_table("warehouse", harness.products.record.table)


async def test_official_payload_fixtures(harness):
    fixtures = json.loads((Path(__file__).parent / "fixtures/metadata-api.json").read_text())
    snapshot = await harness.client.export_metadata()
    assert snapshot.model_dump() == fixtures["export"]
    await provision(harness)
    await harness.client.create_select_permission(
        "warehouse",
        harness.products.record.table,
        DEPARTMENT_ID,
        SelectPermission(columns=("sku", "available_quantity")),
    )
    assert harness.engine.writes() == [
        fixtures["add_source"],
        fixtures["track_table"],
        fixtures["create_select"],
    ]
    assert await harness.client.check_health() is True
    assert [source.model_dump() for source in await harness.client.list_sources()] == [
        {"name": "warehouse", "kind": "postgres"}
    ]


async def test_source_table_permission_idempotency(harness):
    await provision(harness)
    permission = SelectPermission(columns=("sku",))
    await harness.client.create_select_permission(
        "warehouse", harness.products.record.table, DEPARTMENT_ID, permission
    )
    writes = len(harness.engine.writes())
    await provision(harness)
    await harness.client.create_select_permission(
        "warehouse", harness.products.record.table, DEPARTMENT_ID, permission
    )
    assert len(harness.engine.writes()) == writes
    await harness.client.drop_select_permission(
        "warehouse", harness.products.record.table, DEPARTMENT_ID
    )
    await harness.client.drop_select_permission(
        "warehouse", harness.products.record.table, DEPARTMENT_ID
    )
    assert len(harness.engine.writes()) == writes + 1


async def test_drop_permission_when_source_or_table_absent_is_idempotent(harness):
    await harness.client.drop_select_permission(
        "warehouse", harness.products.record.table, DEPARTMENT_ID
    )
    await harness.client.add_postgres_source(
        "warehouse", await harness.secrets.resolve_postgres("warehouse-reader")
    )
    await harness.client.drop_select_permission(
        "warehouse", harness.products.record.table, DEPARTMENT_ID
    )
    assert len(harness.engine.writes()) == 1


async def test_duplicate_source_does_not_replace_configuration(harness):
    await provision(harness)
    harness.engine.source()["configuration"]["connection_info"]["database_url"] = {
        "from_env": "OTHER_DATABASE_URL"
    }
    with pytest.raises(MetadataConflict):
        await harness.client.add_postgres_source(
            "warehouse", await harness.secrets.resolve_postgres("warehouse-reader")
        )
    assert len(harness.engine.writes()) == 2


async def test_source_export_normalized_defaults_are_idempotent(harness):
    await provision(harness)
    source = harness.engine.source()
    source["configuration"]["connection_info"]["pool_settings"].update(
        {
            "connection_lifetime": 600,
            "idle_timeout": 180,
        }
    )
    source["configuration"]["read_replicas"] = None
    source["customization"]["root_fields"]["prefix"] = None
    await provision(harness)
    assert len(harness.engine.writes()) == 2


async def test_source_replica_configuration_is_not_silently_accepted(harness):
    await provision(harness)
    harness.engine.source()["configuration"]["read_replicas"] = [
        {"database_url": {"from_env": "OTHER_DATABASE"}}
    ]
    with pytest.raises(MetadataConflict):
        await provision(harness)


async def test_permission_normalized_defaults_are_idempotent(harness):
    await provision(harness)
    permission = SelectPermission(columns=("sku", "available_quantity"))
    await harness.client.create_select_permission(
        "warehouse", harness.products.record.table, DEPARTMENT_ID, permission
    )
    configured = harness.engine.table()["select_permissions"][0]["permission"]
    configured["computed_fields"] = []
    configured["columns"].reverse()
    configured.pop("allow_aggregations")
    await harness.client.create_select_permission(
        "warehouse", harness.products.record.table, DEPARTMENT_ID, permission
    )
    assert len(harness.engine.writes()) == 3


async def test_unmanaged_permission_not_overwritten(harness):
    await provision(harness)
    harness.engine.table()["select_permissions"] = [
        {
            "role": f"dxos_department_{DEPARTMENT_ID.hex}",
            "permission": {"columns": ["sku"], "filter": {}},
        }
    ]
    with pytest.raises(MetadataConflict):
        await harness.client.drop_select_permission(
            "warehouse", harness.products.record.table, DEPARTMENT_ID
        )


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(503, text="private diagnostics"), HasuraUnavailable),
        (httpx.Response(401, json={"error": "private diagnostics"}), HasuraRejected),
        (httpx.Response(302, headers={"location": "https://elsewhere.invalid"}), HasuraRejected),
        (httpx.Response(409, json={"error": "private diagnostics"}), MetadataConflict),
        (
            httpx.Response(400, json={"code": "already-exists", "error": "private diagnostics"}),
            MetadataConflict,
        ),
        (httpx.Response(200, text="not json"), InvalidMetadata),
        (httpx.Response(200, json=[]), InvalidMetadata),
        (httpx.Response(200, json={"metadata": {}}), InvalidMetadata),
        (
            httpx.Response(200, json={"resource_version": 1, "metadata": {"version": 3}}),
            InvalidMetadata,
        ),
        (httpx.Response(200, json={"code": "access-denied", "error": "private"}), HasuraRejected),
    ],
)
async def test_response_validation(harness, response, expected):
    harness.engine.failures["export_metadata"] = response
    with pytest.raises(expected):
        await harness.client.export_metadata()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ReadTimeout("private diagnostics"), HasuraTimeout),
        (httpx.ConnectError("private diagnostics"), HasuraUnavailable),
    ],
)
async def test_transport_errors_are_sanitized(harness, failure, expected, caplog):
    harness.engine.failures["export_metadata"] = failure
    with pytest.raises(expected) as caught:
        await harness.client.export_metadata()
    assert "private diagnostics" not in str(caught.value) + caplog.text
    assert "only-a-test-admin-secret" not in str(caught.value) + caplog.text


async def test_secret_never_in_metadata_or_repr(harness):
    await provision(harness)
    dumped = json.dumps(harness.engine.requests) + repr(harness.settings)
    assert "test-password" not in dumped
    assert "only-a-test-admin-secret" not in dumped
    assert "WAREHOUSE_READ_DATABASE_URL" in dumped


async def test_upstream_error_body_never_leaks_secret(harness, caplog):
    secret = harness.settings.admin_secret.get_secret_value()
    harness.engine.failures["export_metadata"] = httpx.Response(
        400, json={"code": "access-denied", "error": f"diagnostics include {secret}"}
    )
    with pytest.raises(HasuraRejected) as caught:
        await harness.client.export_metadata()
    assert secret not in str(caught.value) + caplog.text


async def test_optimistic_conflict_does_not_retry_write(harness):
    harness.engine.failures["pg_add_source"] = httpx.Response(409, json={"code": "conflict"})
    with pytest.raises(MetadataConflict):
        await provision(harness)
    assert len(harness.engine.writes()) == 1
    assert not harness.engine.metadata["sources"]


async def test_acknowledgment_without_state_is_failure(harness):
    harness.engine.ack_without_apply = True
    with pytest.raises(InvalidMetadata):
        await provision(harness)


async def test_targeted_reload_and_consistency(harness):
    report = await harness.client.reload_metadata(["warehouse"])
    assert report.is_consistent
    assert harness.engine.requests[-1]["args"] == {
        "reload_sources": ["warehouse"],
        "reload_remote_schemas": False,
        "recreate_event_triggers": False,
    }
    harness.engine.consistent = False
    assert not (await harness.client.reload_metadata(["warehouse"])).is_consistent
    assert (await harness.client.get_inconsistent_metadata()).inconsistent_count == 1
    with pytest.raises(InvalidMetadata):
        await harness.client.require_consistent()


async def test_secret_required(harness):
    with pytest.raises(InvalidConfiguration):
        HasuraMetadataClient(HasuraSettings(_env_file=None), harness.http)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:password@hasura.example.invalid",
        "http://remote.example.invalid",
        "https://hasura.example.invalid/v1/metadata",
        "https://hasura.example.invalid?token=secret",
    ],
)
async def test_reject_unsafe_endpoint(endpoint):
    with pytest.raises(ValidationError):
        HasuraSettings(_env_file=None, endpoint=endpoint)
