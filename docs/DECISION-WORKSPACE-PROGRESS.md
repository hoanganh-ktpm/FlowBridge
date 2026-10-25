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
