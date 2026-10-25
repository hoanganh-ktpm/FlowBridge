# Native owner operations and carrier handoff (M09)

These APIs operate the existing `sales_orders`, Warehouse inventory/receiving/
reservation/dispatch tables, and Logistics `shipments`. They are separate from
the older `wf_e2e_*` delivery workflow. No old fixture guard was removed and no
customer-delivery operation was added to the native process.

The database schema is unchanged: existing Alembic-managed owner tables already
contain the needed versions, receipts, ledger and inbox/outbox. API startup does
not create tables. PostgreSQL migrations remain the setup authority.

## What an operator does

1. Warehouse confirms its preparation policy (a confirmed zero differs from
   unknown) and updates an inbound ETA using its current update timestamp.
2. On physical receipt, Warehouse records received/accepted quantities and any
   rejection reason. Only accepted quantities increase usable stock.
3. Read the confirmed Sales order and inventory versions. Reserve every original
   line atomically. If one SKU is missing, nothing is held. Duplicate-SKU Sales
   lines retain distinct IDs while competing for their combined quantity.
4. Prepare a frozen dispatch manifest. This does not reduce on-hand stock.
5. Logistics reads that manifest and the Sales shipping snapshot, locks the
   carrier's daily capacity row, and records an accepted slot/acceptance reference.
   Acceptance does not mean handoff.
6. An authenticated human Warehouse operator records the actual carrier receiver,
   receipt number and nonfuture handoff time. Machine identities are refused.
   Quantities cannot exceed the protected hold. Stock, hold, negative movements,
   evidence, command receipt and outbox commit together exactly once.
7. Logistics and Sales reconcile through authenticated owner reads and deduplicated
   source evidence. Sales completes only when every original line was handed over;
   its completion time is the latest physical Warehouse handoff.
8. **Kiểm tra bàn giao thực tế** asks Core to read the current scoped owner proof.
   A complete, consistent physical proof can release the approved Flowable wait
   and close the dossier. A booking, forecast, chat message or empty proof cannot.

Actual handoff records the Warehouse operator's attestation and carrier receipt
reference. It is not a cryptographic verification of a carrier signature or a
customer receipt. No nonexistent Core Document endpoint is assumed: document
storage/ACL integration can later attach a separately authorized proof document.

## Permissions and service boundaries

Every new owner API requires a verified identity plus explicit organization,
warehouse and permission assignments in that owner's
`FLOWBRIDGE_BUSINESS_ACTOR_BINDINGS`. Keys are Keycloak **subjects**, whereas
Workspace/Core decision assignments use **Core principal UUIDs**. A platform
administrator does not implicitly receive owner permissions.

For example, one Warehouse subject's binding has this structure (IDs are local
configuration, never roles/passwords stored in business tables):

```json
{
  "<Keycloak subject>": {
    "org_id": "<organization UUID>",
    "warehouse_ids": ["<warehouse UUID>"],
    "permissions": ["operations.stock.reserve", "operations.dispatch.prepare"]
  }
}
```

Native write permissions:

| Owner | Permission |
|---|---|
| Warehouse | `operations.preparation.confirm`, `operations.inbound.update_eta`, `operations.receipts.post`, `operations.stock.reserve`, `operations.dispatch.prepare`, `operations.dispatch.handoff` |
| Logistics | `operations.shipments.book`, `operations.shipments.reconcile` |
| Sales | `operations.orders.reconcile` |

Read permissions are separate: `operations.orders.read`, `operations.inventory.read`,
`operations.inbound.read`, `operations.reservations.read`, `operations.dispatch.read`,
`operations.handoff_proof.read`, `operations.capacity.read`, `operations.shipments.read`.
Cross-owner calls forward the same verified caller token. An operator needs the
necessary scoped read rights at both owners. No service imports another owner's
repository or database URL. Existing human SSO/MFA policy remains active.

Each owner runs with its own database/owner URL, e.g. warehouse_owner on
warehouse_db, in `FLOWBRIDGE_BUSINESS_DATABASE_URL`. Internal read origins come
from `FLOWBRIDGE_BUSINESS_SERVICE_BASES` and currently require explicit loopback
origins. This local profile is not a general production network configuration.

Native owner authority is independent of a particular decision workflow: a real
Warehouse operator can record receiving in its native application. Action
registration alone does not approve a case or grant business rights. The Agent
must not be offered generic write/action-invoke tools. Automated case execution,
impact-driven recovery and case-level progress projection are later integration
work; these owner interfaces do not claim to be a distributed transaction/saga.

## Core capability contract

`contracts/business-operations.actions.json` contains 20 explicit, versioned HTTP
actions with closed input schemas and fixed owner paths. Commands have matching
POST endpoints; read adapters use POST for the existing Action Gateway protocol
while ordinary GET owner reads remain available.

```powershell
.\.venv\Scripts\python.exe -m scripts.business_actions export
# Explicit admin publication; obtains no credentials and executes no business command:
.\.venv\Scripts\python.exe -m scripts.business_actions register --core-url http://127.0.0.1:18000
```

Registration reads an existing admin bearer token from the process environment
`FLOWBRIDGE_ACTION_REGISTRATION_TOKEN`. Keep it out of Git/chat. The owner
applications must already be active in Core and the corresponding service bases/
HTTP hosts must be allowlisted. Registration is not run by export, migration or
API startup. The new definitions have not been published to a hosted/shared Core.

For dossier completion, Core's execution service additionally needs authorized
`warehouse.operations.handoff_proof_read` access, including the downstream native
Sales order read needed to verify original lines. Core validates scope, complete
per-line quantities, unique source event identities, manifest hashes and nonfuture
physical times before sending the BPMN completion message. It reconciles a lost
message response against engine history rather than sending a second message.

## Concurrency, retries and partial work

Reservation locks balances in product order and validates expected stock versions.
Carrier booking locks the shared carrier/day row before counting accepted slots.
ETA uses a locked `updated_at` comparison; preparation uses its policy revision.
Received quantities are capped against the locked plan and already posted receipts.

All commands require Idempotency-Key and authorize scope before a cached receipt
can be read. Retry the exact command/key after a lost response; changed inputs
must not reuse a successful key. Cached command responses describe that command's
result, so re-read current owner state before planning the next step. Exact owner
quantities serialize as decimal strings to preserve physical quantities.

Independent owners can commit at different times. A failure after reserving does
not undo the hold or pretend a shipment exists. Read the reservation, dispatch,
shipment, command receipt and outbox/inbox state, then reconcile/retry the affected
step with its original key. Warehouse remains physical truth if a downstream
projection is temporarily unavailable. Dossier completion stays blocked until
Core can verify the complete current proof. Native source states/receipts exist;
automatic event transport and a consolidated case execution ledger belong to M10.

Each owner exposes `POST /api/v1/{owner}/operations/commands/read` (and a matching
Core action). It requires the original operation permission and organization/
warehouse scope, then returns a committed receipt's status and minimal resource
references. It does not return cached customer data. `UNCONFIRMED` means no committed
receipt was visible, not proof that an in-flight request can never arrive; do not
blindly advance/reissue later steps from that result. Scope is recorded in the
existing receipt resource fields without a new table or migration.

## Verified so far

The disposable PostgreSQL tests cover two competing 8-unit orders with 10 units,
whole-order rollback for a missing SKU, duplicate-SKU lines, receipt rejection/
over-receipt, manifest preparation without stock reduction, carrier capacity,
manual handoff, unique movements and idempotent Sales/Logistics reconciliation.

A separate HTTP test uses the real Core Action Gateway adapter, real native owner
HTTP handlers and three real PostgreSQL databases. IAM and network transport are
stubbed; it is not a live Keycloak/Hasura/browser acceptance run. Flowable tests
separately check physical-proof refusal/acceptance and lost-message reconciliation.
Portal browser tests use mocked APIs and keep an approved case open until server
verification succeeds. M12 performs the combined live acceptance.
