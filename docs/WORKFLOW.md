# Workflow Integration

## Local Business Workflow — 2026-10-24

The current business acceptance profile uses the existing PostgreSQL 16 container and a new
`flowable_db`, owned by `wf_engine`. Flowable OSS 7.2.0 runs at **127.0.0.1:18083** on the local
Docker network, with JDBC/TLS to `postgres:5432/flowable_db`. It does not connect to or copy instances
from the old Supabase engine/profile described in the historical sections below.

The three FastAPI processes bind to 18101 (Sales), 18102 (Warehouse), 18103 (Logistics). Each uses
only its own database/owner. Shared technical auth/HTTP/idempotency helpers live in
`app.platform.business`; no service imports Core or another service. Native Core/worker remain
on 18000 for private Docker callbacks. All test credentials/configuration/evidence are ignored
under `.tmp/business-workflow` and `.tmp/docker-integration/runs/wf_e2e_*`.

```powershell
.venv/Scripts/python.exe -m scripts.database.local upgrade
.venv/Scripts/python.exe -m scripts.business_environment configure
.venv/Scripts/python.exe -m scripts.business_environment up
$env:FLOWBRIDGE_BUSINESS_E2E='1'
$env:FLOWBRIDGE_BUSINESS_WORKFLOW_E2E='1'
.venv/Scripts/python.exe -m pytest tests/e2e/test_business_apis.py tests/e2e/test_business_workflow.py -m e2e -q
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m scripts.database.synthetic validate
```

Prerequisites are the running local PostgreSQL/Hasura and existing local test Keycloak realm.
The tests prepare event-source roles, start native processes, provision explicit Core read grants
and custom actions, then revoke their own grant and remove their own capture triggers. Business
rows and process history are retained for inspection. No reset/demo seed or realm mutation runs.
`scripts.business_environment restart` restarts only the new engine; its state stays in PostgreSQL.

### Business slice and contract boundary

The existing historical Sales/Logistics models stop at physical carrier handoff. Additive heads
`sales_0003` and `logistics_0003` add `wf_e2e_orders` and `wf_e2e_shipments` for delivery semantics;
Warehouse remains at `warehouse_0002` and reuses its actual balances/reservations/dispatch/ledger.
Core remains at `core_0006`. New stock has an opening POSTED receipt and a dedicated org/warehouse
and `wf_e2e_*` SKU prefix. Existing orders, products, quantities and events are never updated.
The two workflow tables do not claim to implement the broader proposed P1 OpenAPI contracts.
Action payloads are defined in `contracts/workflow-business.schema.json`.

| API prefix | Operations |
|---|---|
| `/api/v1/sales/workflow` | Create/read order, bind verified workflow instance, monotonic status projection; DELIVERED verified from Logistics |
| `/api/v1/warehouse/workflow` | Read inventory/reservation, locked all-or-nothing reserve, pack, physical handoff with stock ledger/outbox evidence |
| `/api/v1/logistics/workflow` | Create/read shipment from packed Warehouse dispatch; HANDED_OVER/DELIVERED require Warehouse evidence |

Every mutation checks a verified Keycloak subject and an explicit local org/operation binding
before accessing cached results. PLATFORM_ADMIN is not an implicit business permission. Per-key
command receipts/hash checks commit with domain changes. Per-order locks plus unique constraints
prevent duplicate reservations/shipments even with different retry keys; balances lock in product
order. Warehouse packing and handoff are separate physical actions by its authorized actor.

`order_fulfillment.bpmn` reads the approved `warehouse.stock_availability` Data Product through
`data.get_record`, checks requested quantity, invokes real reservation, waits for Warehouse packing,
creates Logistics shipment and waits for `shipment.delivered`. The final Sales update independently
checks Logistics before the engine reaches End. Only explicitly configured assignee subjects may
be assigned by the starter; completion still checks the actual authenticated assignee. JSON action
requests are serialized by the generic Core adapter; business rules stay in receivers.

### Event path and evidence

IT Hasura trigger configuration and Workflow subscriptions are separate. Event SQL roles have
SELECT/TRIGGER only on the two workflow tables and own their local `hdb_catalog`. Hasura's capture
helper is SECURITY INVOKER, so domain writers receive only schema usage and INSERT into event_log.
The six existing product views stay read-only. Source names are fixed in an authenticated header
because this Hasura webhook envelope omits source in its body.

The path tested is PostgreSQL INSERT/UPDATE → actual Hasura event_log/webhook → authenticated static
header relay → newly computed body HMAC → durable Event Router → Flowable REST. INSERT starts a
separate engine probe; UPDATE to DELIVERED correlates the full business process. Neither synthetic
event rows nor direct signed-negative probes are counted as PostgreSQL webhook acceptance.
Repeated/old transitions and workflow markers are ignored; event identity/hash dedup survives API
restart. Flowable POST query uses `processBusinessKey` and verifies returned business keys;
the old `businessKey` field was silently ignored by Flowable 7.2 and could broaden correlation.

Live assertions cover different-user task denial, uncompleted/unpacked task, stopped Logistics API,
insufficient stock rollback, retries/hash conflicts, engine/API restart at a User Task, wrong order
correlation, actual START/CORRELATE history, webhook replay after restart, stale events, invalid
header/HMAC, private-column and no-grant denials. The shortage branch ends as WAITING_STOCK, never
as delivered. Full historical-org table hashes are compared before/after each live fixture;
`synthetic validate` now reports its historical-org scope explicitly and excludes other orgs.
The generator reset guard still scans all business rows and refuses unowned E2E records.

Business API writes and Hasura Data Action writes are separate results: **Business API writes are
live-verified; `data.update` remains unavailable (409)**. No Core write-grant bypass is introduced.
Ambiguous pre-existing dispatch intents still fail closed as UNCERTAIN and require reconciliation;
they are not automatically replayed or reported as success.

Verified local run: **`wf_e2e_0b357f3c8afd`**. Its ignored `resources.json` and
`.tmp/business-workflow/final-evidence.json` retain assertions and non-secret references:

| Check | Executed result |
|---|---|
| Offline/default regression | 841 passed, 71 integration/E2E deselected |
| Disposable PostgreSQL migrations/constraints/recovery | 43 passed |
| Real Business APIs + full Workflow/Events/security | 5 passed in 171.81 seconds |
| Engine + persistence | REST 7.2.0; schema 7.2.0.2; same User Task after engine/API restart |
| Main process | `35f7347e-cf5c-11f1-9813-723d5063c9d5`, history End and Sales DELIVERED |
| Order / shipment | `8850508d-d867-48bb-990f-4ddae8b60801` / `7e55a70f-36b9-4348-b62b-32820deae397` |
| History/data access | Original three synthetic-org hashes unchanged; 6 products ACTIVE, 0 grants ACTIVE after cleanup |
| Source quality | Ruff and Alembic model comparison passed |

For standalone Business API inspection, use `scripts.business_environment prepare`, read the
ignored current-run directory, then run in separate terminals (replace `<run>`):

```powershell
.venv/Scripts/python.exe -m uvicorn app.runtime:create_app --factory --app-dir services/sales-api --env-file <run>/sales.env --port 18101
.venv/Scripts/python.exe -m uvicorn app.runtime:create_app --factory --app-dir services/warehouse-api --env-file <run>/warehouse.env --port 18102
.venv/Scripts/python.exe -m uvicorn app.runtime:create_app --factory --app-dir services/logistics-api --env-file <run>/logistics.env --port 18103
```

Local execution tokens are short-lived and refreshed by configure/test setup. This is a bounded
delivery vertical slice with explicit local subject bindings, not a general delegation system or ERP.

## Historical read-only acceptance profile

## Ownership And Scope

The main native factory mounts `/api/v1/workflows` using existing identity, error, correlation and
private-response middleware. Routes remain visible in OpenAPI when the runtime is disabled and
return dependency-not-ready rather than falling back to an in-memory engine.

| Owner | Responsibility |
|---|---|
| Platform Workflow | Flowable REST adapter, HTTP router, action contracts, HMAC/event dispatch ports |
| Core composition | Existing principal/grant policy, Hasura Data Product reads, durable registry/dispatch intent |
| Flowable | BPMN definitions, execution state, User Tasks, subscriptions and process history |
| Business APIs | Business authorization, mutation, reservation/shipment transactions and command idempotency |

`workflow_actions` stores versioned custom action definitions. `workflow_event_inbox` stores hashes,
dispatch intent, deduplication results and run ownership references; it is not a second workflow engine.
An intent is committed before calling Flowable. Ambiguous dispatches remain `DISPATCHING`/`UNCERTAIN`
and are not automatically retried; operator reconciliation is required to avoid duplicate effects.

These additive Workflow routes do not claim completion of the separate P1
`/api/v1/platform/workflow-runs` contract or general business-initiator delegation.

## Authorization

- Administrative operations resolve the active Core principal and `PLATFORM_ADMIN`; token scopes or client names do not grant administrative authority.
- `data.get_record` and `data.query` reuse the existing governed query pipeline, current department grants, column restrictions and non-admin Hasura JWT. No direct business database access or Hasura admin mutation is introduced.
- `data.update/create/delete`, `event.publish` and notifications remain unavailable where the corresponding authorized business/publisher boundary is absent. They must not return fabricated success.
- The current native start profile binds caller and configured engine token to the **same verified identity** and checks data access before starting. It does not impersonate another user or implement cross-user/service delegation. Tokens are not stored as variables or returned by status endpoints.
- Tasks are listed for the verified assignee only; unassigned tasks and admin role alone do not authorize completion. Disabled Core principals are rejected even when their Keycloak token is still valid.
- HTTP actions use configured service references/host allowlists, per-call credentials, explicit timeouts, no redirects and validated input/output schemas.

## Runtime

See [Flowable runtime](../infra/flowable/README.md) for the pinned Docker profile and explicit storage/TLS bootstrap.
Core and Worker remain native; no other component is containerized by this profile.
Core migrations are never run at application startup. The approved integration database now has
`core_0006`; business migrations are unchanged.

Enable `FLOWBRIDGE_WORKFLOW_ENABLED=true` only with actual Flowable credentials and durable Core
storage. Configure the exact `FLOWBRIDGE_WORKFLOW_ALLOWED_WORKFLOWS`, service references/hosts,
HMAC secret/subscriptions and the short-lived execution token explicitly. Defaults are disabled/empty.
The test profile allows a longer engine REST timeout for synchronous HTTP tasks on remote Supabase.

For a native API using an explicitly prepared local environment:

```powershell
.venv/Scripts/python.exe -m uvicorn app.core.runtime:create_app --factory --env-file .tmp/docker-integration/runs/<owned-run>/core.env --host 0.0.0.0 --port 18000
.venv/Scripts/python.exe -c "from dotenv import load_dotenv; load_dotenv('.tmp/docker-integration/runs/<owned-run>/core.env', override=True); from app.core.runtime import main; main()" worker
```

Run the API and Worker in separate terminals. Bind `0.0.0.0` only for this private container callback
test with restrictive host firewall rules. The execution token in native/Docker configuration must
be synchronized and unexpired; the E2E runner obtains a new test token before each fresh run.

## Verification Commands

```powershell
.venv/Scripts/python.exe -m pytest -q
$env:FLOWBRIDGE_FLOWABLE_INTEGRATION_ENABLED='1'
.venv/Scripts/python.exe -m pytest tests/e2e/test_workflow_runtime.py -m integration -q
$env:FLOWBRIDGE_WORKFLOW_DATA_E2E_ENABLED='1'
.venv/Scripts/python.exe -m pytest tests/e2e/test_workflow_data_sharing.py -m e2e -q
```

The default suite excludes integration/E2E. Platform unit tests use `InMemoryFlowable` or HTTP
transport doubles and **do not prove engine execution**. The live runtime probe starts the main
native API, uses real Keycloak tokens, executes an HTTP Service Task/Gateway/User Task/message on
Flowable 7.2.0, restarts the API/engine and checks persistence/history.

The opt-in Data Sharing test creates separately identified test users/principals/departments,
application/product/contract/grant/action/inbox records and Hasura metadata. It reads existing
Warehouse rows and compares all three business databases' row counts/content hashes before/after.
It never reactivates old fixtures or grants. Identity credentials and per-run evidence are ignored
under `.tmp/flowable-integration` and `.tmp/docker-integration/runs/workflow-*`.

## Acceptance Boundary

Runtime probe evidence is independent of the Data Sharing result. Record actual pytest results and
per-run `resources.json`; do not infer E2E success from healthchecks, compilation or XML parsing.
The read-only review fixture is not warehouse reservation or logistics fulfillment.

### Verified Run — 2026-10-24

| Milestone | Result | Evidence |
|---|---|---|
| M1 — Code/API | PASS | 815 offline tests passed, 59 integration/E2E deselected; unique OpenAPI operations; main native API served Workflow routes |
| M2 — Runtime | PASS | `test_workflow_runtime.py`: 1 passed; REST 7.2.0, authenticated access, HTTP/Gateway/User Task/message and API/engine restart |
| M3 — Integration | PASS, read-only | `test_workflow_data_sharing.py`: 1 passed in 278.12s; actual Core → Flowable → Action Gateway → Hasura; custom HTTP action calls the real governed Core query endpoint |
| M4 — Workflow | PASS, read-only | Human-assigned review completes and process history confirms End; this is not business order fulfillment |
| M5 — Events | PASS, signed HTTP ingress | START/CORRELATE target expected instances; replay after restarts is deduped; changed payload conflicts; Core registry/inbox survive process restart |
| Security | PASS | `test_workflow_security.py`: 1 passed; admin without data grant and outsider denied; spoofed admin headers and invalid HMAC rejected |
| Business writes | BLOCKED/unavailable | `data.update` returns INVALID_STATE; no Business API write boundary is fabricated |

The successful Data Sharing run is `workflow-data-8928926b3ed1`; its ignored `resources.json`
records product/grant/instance/task references and successful business-database hash comparison.
The dedicated engine role owns `flowable_runtime`, client TLS is active with the explicitly approved
`require` exception, and it has no SELECT privilege on Core `principals`, Warehouse `products` or
Sales `customers`. Engine schema version observed: `7.2.0.2`; REST runtime version: `7.2.0`.

To repeat the negative checks on an owned successful run without reprovisioning data:

```powershell
$env:FLOWBRIDGE_WORKFLOW_SECURITY_RUN_DIR=(Resolve-Path '.tmp/docker-integration/runs/workflow-data-8928926b3ed1').Path
.venv/Scripts/python.exe -m pytest tests/e2e/test_workflow_security.py -m e2e -q
```

`workflows/order_fulfillment.bpmn` requires registered Business APIs, an inventory contract exposing
`available_quantity`, trusted assignment and serialized shipment input. Those business bindings are
not supplied by the read-only acceptance profile; the business workflow is not accepted as complete.
Normalized, signed HTTP events do not prove a PostgreSQL CDC → Hasura trigger → signing-relay path.
A static Hasura event header is not a body HMAC; that relay must be implemented/verified separately.

Before production: restore `verify-full` with the project CA, replace the test token provisioning
with lifecycle/delegation appropriate to Business API policies, establish inbox reconciliation/retention,
and replace bounded P0 run-reference scans with indexed lookup as volume grows.
