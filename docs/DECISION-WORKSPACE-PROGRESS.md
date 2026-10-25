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
