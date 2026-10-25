# Decision Workspace — implementation ledger

Scope: one warehouse per analysis; Sales/Warehouse/Logistics remain independent
data owners. Successful completion ends at evidenced carrier handoff, not customer
delivery. Each milestone requires its own tests before dependent features ship.

## M00 — isolated test baseline (25 October 2026)

Implemented a disposable PostgreSQL runner that never reads `.env`, removes
inherited application/database/E2E target variables, publishes only on loopback
using an ephemeral port, and cleans up only a container matching its run ID,
label and full container ID. Workspace uses its existing database/role guards;
the owner is not a superuser. Test credentials and resource ledgers stay in
ignored `.tmp/decision-tests/`. CI covers PostgreSQL 16 and 17.

Validation performed locally:

- Baseline fast Python suite: **1008 passed, 73 deselected**. Deselected live tests
  were not executed and are not counted as passed.
- Runner guard tests: external host, wrong role/database, query override, missing
  port and inherited shared targets rejected.
- Real PostgreSQL 16: migration and repeated upgrade, persistence across repository
  restart, owner and organization isolation **passed** using the dedicated runner.
- Local Keycloak discovery accepted the configured CA and rejected an untrusted
  CA. This was a read-only TLS check, not a full user login/workflow acceptance.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/platform/test_decision_test_environment.py -q
.\scripts\test_decision_workspace_local.ps1
```

The historical `scripts/test_e2e.ps1` targets a different Supabase test profile;
it must not be used as the default Decision Workspace test launcher.

## M01 — canonical decision metrics (25 October 2026)

Implemented one definition of physical supply shortage for decision measures,
business summaries, comparison deltas, saved cards and proposal text. Event-time
deficit remains visible even when an afternoon receipt clears end-of-day unmet
demand. Whole-order blocking caused by another SKU remains distinct from a
physical shortage of the selected SKU. New results carry explicit metric version
and event-time/day-end basis; ambiguous saved legacy results ask for recomputation.

Validation performed locally:

- Domain and runner guards: **111 passed** at this milestone.
- Portal Vitest: **15 passed**.
- Portal TypeScript and production build: **passed**.
- Mocked browser Workspace suite: **26 passed**, including two new saved-result
  regressions. These browser tests do not prove live SSO/Hasura/Flowable integration.

M02–M12 are not accepted merely because these tests pass. In particular,
cross-application live reads, the new multi-role decision workflow, guarded
execution and verified outcomes require separate evidence.

## M02 — governed source identities and live reads (25 October 2026)

Added validation for stock coverage, stock arithmetic, SKU/UOM, duplicated lines
and Sales/commitment identity mismatches. Missing stock facts are unknown, not
silently treated as zero. Each source has its own read window, row count, contract
reference and input hash; no distributed atomic snapshot is claimed.

The older local test's password-only human tokens correctly failed MFA checks.
The API profile now provisions explicitly bound, short-lived machine clients in
the local test realm. It does not change browser/MFA policy or impersonate humans.
Owned clients are removed with a renewed admin token after testing; audit-linked
Core principals are disabled and memberships revoked. Identity ledgers contain no
credentials. Browser MFA acceptance remains a separate later gate.

Validation performed locally:

- Domain/guard/identity checks: **121 passed** before M03 additions.
- Real local PostgreSQL + Keycloak + Core/worker + Hasura: **2 passed**. Published
  all eleven source views, checked SQL-view values against governed HTTP results,
  changed/restored a scoped source balance, tested outsider/admin denial and grant
  revocation, and read all nine Workspace planning roles through CoreConnector.
- A first teardown exceeded the short master-realm admin token lifetime. The
  helper now renews that token for cleanup; the full rerun passed including cleanup.
- Seeded 114 internally linked baseline fixture rows into previously empty **local**
  business databases. No hosted/Supabase targets were used.

Run the live API profile explicitly, with the local engines already configured:

```powershell
.\.venv\Scripts\python.exe -m scripts.decision_local_acceptance
```

This profile temporarily creates/revokes local grants and tests a restored source
update. It is not a read-only health check or a full human-user workflow test.

## M03 — historical analytics and dashboard foundation (25 October 2026)

Retained the existing historical views and added a deterministic priority briefing
with source-read time and the nearest orders needing coordination. It navigates
to the existing source-backed order detail view and does not invent an ML score.
Missing optional sources remain explicit. The later case/task integration and
five-zone dashboard are still tracked under M07–M11.

Validation: **35 historical/report/analytics tests passed**, including numeric
oracles for moving averages, period growth and stock snapshots; **16 frontend
unit tests passed**, **4 targeted mocked-browser tests passed** (priority action,
desktop/mobile overview, order navigation and missing receipts); TypeScript and
production build passed. Full domain plus environment/identity guards: **124 passed**.

## M04 — forecast validation and separate corrected-P50 holdout (25 October 2026)

Added a final chronological holdout after earlier residual calibration. The
reported P50 quality has disjoint training/calibration and evaluation dates,
per-method MAE/WAPE and empirical interval coverage. Short histories explicitly
return no independent post-calibration score. Existing base-model validation is
kept separately and labeled accordingly. Historical holdout coverage is not a
future probability guarantee or a measurement of the final refitted model.

Validation: **122 Workspace domain tests passed** (129 including runner/identity
guards), five new holdout regressions include unchanged predictions when only
held-out targets change and preservation of the unseen gap. **16 frontend unit
tests**, TypeScript and **four targeted mocked-browser tests passed**, including
the P50-quality label and strict ML failure behavior.

## M05 — preparation cutoff and handoff conditions (25 October 2026)

Added additive Warehouse migration `warehouse_0005`: nullable preparation time,
confirmed actor/time and policy revision on the warehouse, with a separate safe
published view. Missing policy stays unknown; a confirmed zero is distinct.
The original table/migration fingerprint fixture is preserved; a reviewed additive
overlay records the changed warehouse model and the new revision.

Planning and scenarios use the same warehouse policy to check stock availability
before `handoff deadline − preparation time`, while preserving the original
handoff time for display and carrier comparison. Supply, preparation, schedule
and carrier capacity are separate conditions. A feasible projection is never
reported as actual handoff. Portal shows the preparation cutoff or its absence.
The owner write interface and workflow confirmation remain in M08/M09.

Validation: **1043 fast Python tests passed, 76 deselected**; **45 real isolated
PostgreSQL tests passed**, covering migrations/constraints and both database/schema
layouts; **two live Core/Hasura tests passed** with the new preparation view added
through Core commands and ten Workspace source roles read. Targeted browser checks
for preparation policy/cutoff, sensitivity and order navigation passed; **16 frontend
unit tests**, TypeScript and production build passed.

The broader database run exposed two old tests assuming that all published-view
migrations permit destructive downgrade. Those migrations remain protected. Fresh
install tests now assert the downgrade refusal and reset only allowlisted objects
in fixture-created loopback databases, without CASCADE, preserving neighbor schemas.

Applied the additive migration and published the new view only to the existing
local development stack. No confirmed preparation value was invented or written
to business data, and no hosted/Supabase deployment was made.

## M06 — exact-hour simulations and conditional recovery (25 October 2026)

Added optional timezone-aware arrival timestamps with date/snapshot validation.
The default date-only mode remains explicit day-start/snapshot planning. Bounded
recovery suggestions use the same ledger and show quantity/cutoff, remaining
shortage/at-risk orders and required Warehouse/Logistics confirmations. A later
handoff reference needs Sales and carrier agreement and cannot be applied as a
business command. An already sufficient plan does not invent a recovery purchase.

Portal can fill a suggested quantity/date/time without calculating, saving or
executing anything automatically. Editing invalidates the old result; explicit
comparison is required. Selecting a day-only matrix cell clears a previously
entered time. Export includes the actual arrival-time assumption. Updated the
checked-in OpenAPI contract, and included preparation policy in input fingerprints.

Validation: **137 Workspace domain tests passed**; **18 frontend unit tests passed**;
TypeScript and production build passed; **four targeted mocked-browser tests passed**
including recovery prefill/no automatic writes/export time and matrix semantics;
**402 contract/OpenAPI tests passed**. There is still no approval or business write
claim from a simulation; those are separate M07–M10 milestones.

## M07 — dossier persistence and governed collaboration (25 October 2026)

Added additive `workspace_0002`, SQLAlchemy tables, transactional repository and
HTTP endpoints for five dossier tables. Active cases are unique per scoped order;
retries deduplicate, edits use expected versions and append revisions/events.
Explicit deployment-owned operational assignments separate the proposer/verifiers
from the manager. Current Core source authorization is checked on case/history
reads; original forecast/simulation results remain private. Case copies filter
orders/SKU points and retain explicitly warehouse-wide scenario metrics.

Portal supports choosing an affected order, saving a draft dossier, reading its
revision/event history and attaching another saved scenario as a new revision.
Retries retain the command key. Feature availability depends on server assignments;
unconfigured deployments retain the existing analytics experience. See
`docs/DECISION-DOSSIERS.md` for configuration, bounds and user steps.

Validation: **141 Workspace domain tests passed**; **3 real PostgreSQL/HTTP tests
passed**, including upgrade from workspace_0001 containing an existing private
result, repeated upgrade, concurrent create/edit, scoped FK, restart, private run
isolation and revoked-source denial. The earlier broader disposable run passed
**46 database tests** before adding the third HTTP/upgrade test. **397 contracts
passed**, **18 frontend unit tests**, TypeScript/build and two targeted mocked
browser tests passed (dossier retry/draft semantics and recovery regression).

This is the draft persistence/collaboration part of M07. CAS-08 (process-start
crash reconciliation), submission/approval and live multi-role Flowable acceptance
remain dependent on M08. No draft is described as freshly validated or approved.
No hosted/shared database was changed for this milestone.

## M08 — multi-role Flowable validation and approval (25 October 2026)

Added the separate `supply_handover_exception` BPMN template: parallel Warehouse
and Logistics checks, manager approve/return/reject, coordinator reminders, and a
wait for actual third-party carrier handoff. Existing templates are unchanged.
Tasks are directly assigned by trusted principal mapping; no self-assignment,
admin override or implicit group claim is exposed. Return/reject requires a reason.

Added a machine-only Core workflow port and renewable Workspace OAuth client.
Core independently resolves active principals, operational scope and distinct
manager identity. The generic task-completion endpoint refuses this template.
Workspace stores a durable submission intent, immutable revision binding, check
results and task receipts. Engine-marker/history reconciliation handles lost start
and task responses without blindly redispatching. A slower status observation
cannot regress the current revision. The Portal submits, shows assigned tasks,
records reasons and keeps approval separate from physical completion.

Minimum pre-command revalidation now bypasses the input cache and checks current
history, scoped stock/orders/commitments/inbound/preparation, schedule and relevant
capacity. A stale revision cannot be confirmed/approved; an authorized reviewer
can return/reject it explicitly as stale. Full impact classification, events and
the source-change-during-uncertain-start recovery path remain in M10.

Validation: **9 isolated PostgreSQL/Flowable integration tests passed**, including
the Workspace HTTP + real PostgreSQL + Core workflow logic + actual Flowable bridge;
lost engine task response, start-response retry, role denial, stale approval and
reasoned return. The source/IAM boundaries in that bridge are mocked, not live SSO.
Five actual-engine cases cover parallelism, all manager branches, timers and engine
restart. **543 targeted domain/Core/contract tests passed**; the final full fast
run passed **1059 tests, 84 deselected**.
Three targeted mocked-browser cases passed, including manager stale/return behavior;
TypeScript/build passed. Detailed configuration and boundaries are documented in
`docs/DECISION-WORKFLOW.md`; the isolated runner is added to CI.

No production assignment, hosted database or human MFA policy was modified.
M09–M12 and the full four-human live acceptance are not yet complete.

## M09 — native owner transactions and evidenced handoff (25 October 2026)

Added owner APIs on the actual Sales/Warehouse/Logistics tables without removing
the old wf_e2e guards or changing their delivery semantics. Warehouse can confirm
preparation, update ETA, post accepted receipts, reserve an entire original order,
freeze a dispatch and record human-attested carrier handoff. Logistics serializes
carrier/day bookings and reconciles actual Warehouse evidence. Sales closes only
after all original line quantities are physically handed over, using the latest
source handoff time. Ledger, holds, receipts and inbox/outbox remain owner-local
transactions. Physical handoff requires a human receiver/receipt/time entry and
refuses machine identities. The carrier receipt is an attestation/reference, not
a cryptographically verified external signature or customer receipt.

Exported 20 fixed, versioned native HTTP action contracts and an explicit admin
registration CLI. Native permissions include organization and warehouse scope;
platform admin alone grants nothing. Quantities preserve decimal strings. Scoped
command-receipt lookup returns minimal references; missing receipts remain
UNCONFIRMED, not a claim that a request cannot still commit.

Core can read the registered owner handoff-proof action and release the approved
Flowable message wait only after scoped per-line quantities, event identities,
manifest hashes and nonfuture actual times validate. Lost message responses are
reconciled against engine history. Workspace appends the proof references and
closes the dossier atomically; Portal keeps approval separate from completion.
See `docs/DECISION-OWNER-OPERATIONS.md` for roles, setup and partial-work boundaries.

Validation: **53 isolated PostgreSQL tests passed, one Flowable-only bridge case
skipped in that profile**; these include actual three-owner HTTP handlers through
the Core Action Gateway adapter, competing stock/capacity requests, whole-order
rollback, duplicate-SKU line identity, receipt rejection/over-receipt, handoff
ledger effects and idempotent Sales/Logistics source reconciliation. IAM/network
transport in the HTTP test is stubbed. **10 isolated Flowable/Workspace integration
tests passed** in its dedicated profile, including strict physical-proof gates,
lost completion-message response and persisted case closure. Full fast suite:
**1061 passed, 91 deselected**. Three targeted mocked-browser tests passed,
including refusing premature completion; TypeScript/build passed. The existing
Starlette TestClient/httpx deprecation warning is retained, not hidden.

These are owner capabilities and explicit role commands. Automatic event transport,
consolidated execution/uncertain-start recovery and impact projection are M10;
the live four-human SSO/Hasura/Portal path and hosted deployment are not claimed.
No persistent hosted/shared business data was mutated by these tests.


## M10 — scoped impact checks, source replay and lost-start recovery (25 October 2026)

Source checks now compare a versioned obligation contract after whole-warehouse
allocation: immutable original lines/deadlines, assumptions, forecast history,
preparation, selected-order outcomes and carrier slot. Harmless changes record a
NO_EFFECT check with a distinct observed hash; changed obligations/gates require
revision and block confirmation/approval. Cutoff expiry is checked even when the
raw source hash is unchanged. Checks and before/after diffs are visible in Portal.

Three fixed native event read actions (23 contracts in total) expose scoped,
redacted, checksummed summaries. Explicit sync replays the case window, dedups
across users in the transactional decision-event ledger and retains late/out-of-order
versions without changing case/engine state. Recorded steps remain historical
facts; missing observations never prove an action did not run. Reading retained
source observations rechecks native and Data Product permissions. Replay is
bounded with an incomplete-history warning; no background push worker is claimed.

New workflow starts retain a minimal durable intent. Read-only reconciliation
finds the exact Flowable marker even after source facts changed, validates current
participant assignment and attaches only matching scope/revision. Missing proof
remains UNCONFIRMED, never permission to dispatch a duplicate process. The pending
Portal button uses this reconciliation rather than attempting a new start.

Validation: 54 isolated PostgreSQL tests passed, one real-engine bridge skipped
in that profile; 11 tests passed in the dedicated PostgreSQL/Flowable profile.
Four targeted mocked-browser cases passed; Portal TypeScript/build passed.
Full fast suite passed 1,072 tests (92 deselected), with the two final added
source-boundary cases passing separately. See DECISION-REVALIDATION.md for exact
semantics, setup and limitations. Retained native actions remain independently
authorized ERP operations; no generic autonomous Agent writer was added.

M11–M12 (four-screen workspace, read-only Agent adapter and complete live acceptance)
remain open. Four-human MFA, the shared hosted stack and production readiness are
not inferred from these isolated/stubbed boundary tests.


## M11 — four-screen workspace, assigned work and structured explanations (26 October 2026)

Primary navigation now follows Cần xử lý → Chi tiết & phương án → Việc của tôi →
Theo dõi kết quả; analytical utilities and dossier lookup remain secondary.
The dashboard connects summaries, exceptions, the event-aware daily deficit,
conditional scenarios and work/outcomes. No dashboard value is calculated by an LLM.

The actual Flowable mine-only queue reauthorizes each dossier and warns on partial
or unavailable state. Details remain bound to the revision, with role-specific
confirmation/approval and reasoned return. Outcomes preserve the distinction
between approval, recorded owner actions and proven full carrier handoff.

A bounded Vietnamese intent adapter exposes four read intents and returns the
existing Portal RunResult schema. The existing renderer provides tables/charts;
order-line units remain separate from the selected SKU's warehouse-wide scenario
metric. Unsupported or mutating questions request clarification, not execution.
Current source/revision checks and UI revocation clearing apply. Existing Langflow
nodes were not changed. See DECISION-WORKSPACE-USER-GUIDE.md and the read-port manifest.

Validation: 34 Workspace browser regression cases passed, plus the new task/assistant
case; the final four targeted browser cases passed, including mobile width and
revocation after rendering. TypeScript/build passed. Eight presentation/permission/
queue unit cases passed; the targeted domain/Core suite passed (see local logs).
The dedicated real PostgreSQL/Flowable suite passed 11 tests with additional HTTP
assertions for per-role queues and the canonical explanation endpoint. Source/IAM
transport in that bridge is stubbed; M12 live acceptance remains open.


## Pre-release integration correction — 26 October 2026

A dossier may now contain an explicit proposed carrier and timezone-aware slot.
It is checked against the published carrier/day capacity, original order deadline
and stock-ready time plus confirmed preparation. An unbooked proposal requires
one additional available dispatch slot; a matching committed booking must not be
counted twice. A conflicting carrier booking, missing capacity or insufficient
preparation blocks confirmation. This does not reserve capacity: the real native
booking still serializes against current capacity after approval. The Portal
collects this condition during dossier creation/revision, and the command hash
includes it so a retry cannot silently change a carrier plan.

The BPMN template now waits for either proven handoff or an explicit replan request.
Only the assigned coordinator/manager may request replanning with a reason; Core
rechecks current assignments and exact revision. The old process ends as
NEEDS_REVISION, while owner transactions and historical evidence are preserved.
A lost message response is reconciled without resending, and a completed physical
handoff cannot be converted into a replan. Existing running definitions are not
rewritten: deploy the new definition for new cases; old approved cases without the
replan subscription need explicit operator handling rather than pretending it exists.

Validation: eight slot-domain cases passed, 175 domain/Core cases passed, and
13 isolated PostgreSQL/Flowable cases passed. The full-engine bridge covers both
pre-booked and unbooked-but-feasible proposals, mine-only queues and source changes.
The replan engine case covers role denial, lost response, exact historical end,
removed handoff subscription and assignment revocation. The test restart fixture
now retains Docker's changed dynamic port for following cases.


## M12 — local live acceptance profile and query performance (26 October 2026, in progress)

Four guarded local demo scripts now seed a coherent named 240-day ledger, compose
the existing local infrastructure, register owned identities/grants/actions/BPMN,
and probe actual service OAuth → Workspace → Core → Hasura → PostgreSQL. The scripts
never load legacy hosted targets, reset existing owner data or enroll a human's MFA.
Per-domain/resource ledgers preserve native changes across restarts. Administrative
setup requires already prepared local PostgreSQL/Hasura/Keycloak; a fresh-clone
one-command installer is not claimed. See DECISION-LOCAL-DEMO.md.

An opt-in bounded Core scan reads at most four deterministic 100-row pages with
full business keys, complexity/offset bounds, before/after grant/metadata checks,
and no partial return on drift. Workspace detects duplicate keys across scans,
filters organization/warehouse at Core and reauthorizes before returning results.
Order-ID batches use bounded concurrency and cancel remaining reads on failure.
Scenario inputs now retain the authorized order number, avoiding UUID-only choices.
An opt-in bounded Core database pool retains NullPool as the default. Its PostgreSQL
test checks transaction mode and advisory-lock cleanup after connection reuse.

The live Portal exposed an alias setup defect: a role-based membership ID could
still refer to the generated coordinator after selecting an existing human account.
Membership setup now keys by principal, verifies the actual principal/department
pair, is idempotent and refuses automatic reactivation of revoked memberships.
The actual `minhhung` coordinator can see the shared catalog and use the Workspace.
Portal list cards now follow the refreshed dossier status instead of retaining a
draft/approved label above a newer detail result.

Validation of the current increment: 1,115 fast tests passed (96 integration/e2e
deselected); 54 isolated PostgreSQL tests passed with two explicit profile skips;
two separate PostgreSQL pool/membership tests passed; 13 real PostgreSQL/Flowable
bridge tests passed; 18 Vitest tests, 36 mocked browser cases and the Portal build
passed. Dedicated bridge/source profiles remain distinct from real human MFA.
The existing Starlette/httpx deprecation warning was not hidden.

Two quiet live HTTP probes returned planning in 9.935/9.944 seconds and scenarios
in 6.142/5.809 seconds; directories took 1.986/1.881 seconds. Both retained 1,723
demand rows, 525 open-order lines, 522 commitment rows, the complete named two-line
acceptance order and the same metric result. Earlier measured runs took 41/23
seconds or timed out under the older query composition/load. These measurements
are not concurrency/load-test evidence or a production latency guarantee.

Human acceptance has started on the real Portal: the coordinator created and
submitted SO-PHOIHOP-001; a separately MFA-enrolled Warehouse user saw only their
task and confirmed it. The real assistant returned canonical table/chart output.
Logistics correctly refused the first infeasible/full carrier slot and returned
the dossier with a reason. The coordinator retained version 1 and submitted
version 2 using the explicitly added alternative carrier and proven preparation
cutoff. Existing bookings were not freed or overwritten to make the test pass.

This found two further integration defects. A source-condition refusal now has
DECISION_CONDITIONS_UNMET and specific preparation/capacity reasons instead of a
generic conflict message. Workflow projection also re-decorates caller-specific
actions, so a returned dossier actually shows its coordinator's revision fields.
The real PostgreSQL/Flowable regression verifies the manager cannot revise while
the assigned coordinator can after projection. Two browser checks passed again
for the changed condition message and list/detail completion synchronization;
TypeScript passed after the message change. The final bridge rerun passed 13 cases.

Live four-role sign-in acceptance then passed: Warehouse and Logistics confirmed
version 2 again, and the separately assigned MFA-enrolled manager approved that
exact revision. The old confirmations were not reused. The mine-only queues cleared
each completed task and showed the manager task only after both confirmations.
These are four role accounts operated during the demo, not four independently
observed people or a production separation-of-duties audit.

The real completion check reached the native Sales/Warehouse read APIs and refused
completion because there was no full physical Warehouse handoff proof. The case
remains APPROVED; neither a shipment nor a physical stock transaction was invented
by approval. Local screenshots/observation notes are ignored artifacts, not secrets
or generated reports committed to Git.

Positive native physical handoff with the real human/owner integrations, live
source-change/revoke scenarios and the final timed walkthrough remain pending.
M12 is not marked complete.
