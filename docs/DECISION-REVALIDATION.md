# Decision impact, source observations and recovery

Implemented milestone M10, 25 October 2026. This extends, rather than replaces,
the Core/Flowable authority and the native owner transaction boundaries.

## Validity is a current source check

Before submit, verifier confirmation or manager approval, Workspace bypasses its
snapshot cache and rereads all governed dependencies under the caller's current
grants. Whole-warehouse allocation is rerun, then the selected order is compared.
An event is not required to detect an obsolete proposal. Expiry of a cutoff also
invalidates an obligation even if no source row or raw input hash has changed.

The immutable `decision_obligations_v1` contract includes original line identities,
quantities, units and deadline; saved action assumptions; forecast-history hash;
preparation duration; the selected order's allocation/shortages; and its carrier
schedule. Opening-stock readiness is compared against its cutoff, not the changing
instant at which a read occurred. The checks require the same Warehouse and
Logistics gates. Corrected forecast history, changed preparation, a different
carrier slot or changed original lines cannot be silently accepted just because
a summary KPI is unchanged.

* `UNCHANGED`: current facts and obligations match.
* `NO_EFFECT`: some dependency changed, but recomputed obligations and both gates
  are identical. This includes an unrelated operational SKU or metadata change
  that leaves this order unaffected; rows are not discarded before allocation.
  The check stores the observed hash separately and retains the approved revision's
  identity when calling Core. It never overwrites the original evidence.
* `REQUIRES_REVISION`: an obligation or gate changed, or old evidence lacks the
  versioned comparison contract. The command is blocked with a structured diff.
  Return the submitted proposal with a reason, recompute, append a revision and
  repeat verification/approval. Improvements that change obligations are also
  conservative new revisions, not automatic approvals.

`POST /decisions/{id}/revalidate` performs and records a check.
`GET /decisions/{id}/checks` returns the newest 100 checks of the current revision,
with `has_more`. Current assignments and source permissions are mandatory for
both. A failed read is UNAVAILABLE/DENIED, never a claim of validity. Native ERP
commands remain independently authorized capabilities; every native write uses
its owner-local locks, expected versions and current source checks described in
DECISION-OWNER-OPERATIONS.md. No unrestricted Agent writer or automatic
cross-database case executor is introduced here.

## Authenticated pull and minimum replay

Register the **23** contracts using `scripts.business_actions`. The three new
fixed `*.operations.events_read` actions expose bounded native outbox summaries.
At each owner grant `operations.events.read` with organization and warehouse scope
only to the appropriate human/service bindings. Action registration alone grants
no native permission. Workspace uses the current caller's bearer through Core;
there is no public browser-supplied event intake or source DB credential.

`POST /decisions/{id}/sync-events` explicitly replays each owner from one minute
before case creation. It uses timestamp/UUID keyset pages, at most 1,000 events per
owner per call. Reaching the cap warns that history is incomplete. A cursor is
only a paging aid: every later sync starts at the beginning so delayed commits
below a previous cursor are still recovered. Source revalidation is authoritative
even when no events arrive, publication is delayed, or replay is incomplete.

Only authenticated owner summaries with a valid canonical checksum, supported
schema, producer and scope enter the existing transactional decision event ledger.
The receipt is `SOURCE_OBSERVED`, with stable producer/event identity and version
conflict checks under the case lock. Duplicate delivery across two users has one
effect. Early and out-of-order versions remain historical observations; they do
not overwrite current state or complete a task. Original source payload hashes
are retained, but recipient contact/address and carrier receiver/receipt bodies
are excluded from summaries. The original envelope remains with its owner.

There is **no background push worker** in this milestone. The Portal offers an
explicit sync and immediately refreshes its source-event history. Matching order
correlations show recorded native steps, including partial work. An absent event
is explicitly not proof a command did not run; use the scoped native command
receipt read and owner state. Correcting a proposal never pretends to roll back
actual owner transactions. Final case completion still requires the separate
per-line handoff proof and approved Flowable message wait.

Reading a persisted source-observation timeline rechecks native feed permission
and the exact referenced revision's governed sources. Revoke or unavailable
authorization fails closed; the old source observation is not returned.

## Lost workflow start response

New starts durably record a minimal intent before contacting Flowable. Core's
machine-only `/decision-runs/commands/reconcile-start` verifies current delegated
participant assignment and locates the existing engine process using the exact
case/revision marker. This reconciliation does not need stale facts to become
valid and never dispatches a second process. Workspace attaches only matching
organization/warehouse/order/case/revision results.

The Portal's pending-start button calls `POST /decisions/{id}/reconcile-start`.
FOUND recovers the process; UNCONFIRMED remains blocked. Neither a missing Core
receipt nor an empty engine lookup authorizes a replacement start. Approval still
requires a new current source check. Old uncertain starts without a stored intent
require explicit operator reconciliation, not an invented migration of evidence.

## Validation and boundaries

* PostgreSQL: 54 passed, 1 skipped (the real-Flowable bridge is run separately),
  including actual owner handlers through an HTTP Action Gateway adapter, native
  feed pagination/redaction, inbox concurrency across users, conflicting versions,
  late events and restart persistence.
* Dedicated PostgreSQL/Flowable: 11 passed, including lost start response plus
  changed facts, harmless revalidation, blocked changed line quantities and
  reasoned return of the actual manager task.
* Four targeted mocked-browser cases passed; Portal TypeScript/build passed.
* Full fast suite: 1,072 passed before the final two event-boundary unit cases;
  those two also passed in their targeted run. Final counts are recorded in
  DECISION-WORKSPACE-PROGRESS.md.

IAM/source transport in integration fixtures is stubbed where explicitly named.
These tests do not constitute four-human Keycloak MFA/Hasura/Portal acceptance,
nor deployment to shared Supabase. Events are source observations, not external
carrier signatures or a cross-database transaction/rollback mechanism.
