# Decision dossiers (M07)

Apply the additive Workspace Alembic migration `workspace_0002` before enabling
dossiers. It creates five Layer 2 tables, preserving business databases, Core
identities and existing private owner scope on forecasts/simulations. API startup
does not run migrations.

Operational assignments are deployment-owned configuration, additional to current
Core read grants, in `FLOWBRIDGE_WORKSPACE_DECISION_BINDINGS`:

```json
{
  "<Core principal UUID>": {
    "org_id": "<organization UUID>",
    "warehouse_ids": ["<warehouse UUID>"],
    "roles": ["coordinator"]
  }
}
```

Roles: `coordinator`, `warehouse`, `logistics`, `manager`. Configure Warehouse and
Logistics participants and a manager separate from proposer/verifiers. These are
Core principal IDs, not Keycloak subjects. The browser cannot choose its role or
participants. Removing an assignment denies access despite the historic roster.
PLATFORM_ADMIN does not imply a business role. Every participant also needs current
Core source grants; unavailable or revoked sources never fall back to saved data.
No default assignment is invented. A future directory resolver can replace this
explicit assignment configuration without changing recorded principal IDs.

Unconfigured deployments report `enabled: false` from `/decision-capabilities`;
analytics continue working and Portal hides dossier actions.

## API and transactions

Under `/api/v1/data-workspace`:

- `GET /decision-capabilities`: caller assignments/feature availability.
- `POST /decisions`: saved simulation UUID, order UUID, descriptive title.
- `GET /decisions`: 100 most recently updated, currently authorized cases.
- `GET /decisions/{id}`: current revision and participants.
- `POST /decisions/{id}/revisions`: saved simulation UUID, expected case version.
- `GET /decisions/{id}/revisions`: 100 most recent revisions, newest first.
- `GET /decisions/{id}/timeline`: first 500 events.

Lists include `has_more`; no truncated list claims completeness. Both POSTs require
a bounded printable `Idempotency-Key`. Same-command retries return the same case
reference/current state; changed commands with that key conflict. Case, revision,
participants and event changes are atomic. Concurrent edits use a row lock and
expected version. Only one active case exists per organization/warehouse/order.
No endpoint edits/deletes an existing revision. This is API immutability, not
protection against a database owner manually editing SQL.

Private source runs stay private. Dossiers copy only the selected order and SKU
forecast points, alongside explicitly warehouse-wide comparison metrics and
governed source references. Case/history/timeline reads recheck current grants.
Organization/participant mismatch returns 404; withdrawn assignments/grants deny
access. No raw source rows or credentials are stored in these five tables.

## User path and boundary

1. Compare and explicitly save a scenario in **Phương án nhập hàng**.
2. Choose an unallocated order contained in it and enter the dossier title.
3. Save it; **Hồ sơ quyết định** lists assigned cases, revisions and timeline.
4. Compare/save another scenario for the same order, then add a new revision.

M07 stores drafts. `requires_revalidation` stays true. There is no submit, approve,
execute or completion route. Flowable owns those tasks in M08. The workflow crash
reconciliation test CAS-08 belongs to that integration and is not claimed passed
by a draft database test. Fully allocated orders without an unallocated planner
record are not yet eligible for this scenario-derived operation.

## Verification

```powershell
.\.venv\Scripts\python.exe -m scripts.decision_test_environment --image postgres:16
.\.venv\Scripts\python.exe -m pytest tests/domain/data_workspace -q
```

PostgreSQL checks cover existing-result upgrade/replay, duplicate commands,
active-order uniqueness, concurrent revisions, cross-org FK, restart and private
run isolation. HTTP checks use real PostgreSQL and a mocked Core boundary. Portal
browser checks use mocked APIs and verify retry keys/draft semantics. These are
not live human SSO or Flowable acceptance tests.
