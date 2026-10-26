# Release readiness — v0.1.0-beta.1

Assessment date: 2026-10-26 (Asia/Saigon)
Decision: **READY WITH LIMITATIONS** for a GitHub source pre-release; not approved here for tag/push/publication or production use.

## A. Release scope

Completed Layer 1 scope: Core API/Worker; application/source/product/contract lifecycles; access governance; Keycloak authentication bootstrap and token validation; Hasura provisioning and governed reads; Flowable integration; seven-database PostgreSQL topology; migrations; deployment CLI; doctor, smoke, acceptance, and source verification.

Experimental: Portal, standalone Windows executable, encrypted backup/restore, and Data Workspace. Planned: production ingress/TLS automation, external PostgreSQL, automated upgrades/rollback, stable Layer 2 analytics, and independent Linux acceptance.

## B. Documentation

- Rewrote the root README in Vietnamese with product positioning, a Layer 1 architecture diagram, explicit Layer 2 status, scope table, verified Quick Start, endpoints, example, project structure, and documentation map.
- Replaced release-level Architecture, Deployment, Testing, Contributing, and Changelog guidance.
- Added `SECURITY.md`, draft release notes, and this readiness report.
- Updated Portal and deployment implementation docs to state port `5173`, DEMO/NOT CONNECTED boundaries, persistence, and safe cleanup semantics.
- Retained deeper design documents; release-level documents are authoritative where old descriptions conflict.

### Working-tree provenance

The branch already contained uncommitted Layer 1 installation work before release preparation: backend runtime/settings, `deployment.example.yaml`, PostgreSQL bootstrap, deployment render/config/smoke/readiness and database scripts, `portal/vite.config.ts`, deployment regression tests, fresh-install reports, `Dockerfile.core`, and the acceptance runner/tests. `check_tang1.py` was a separate pre-existing untracked file and remains untouched/excluded from the artifact.

Release preparation created or revised the root README, changelog, contributing/security guidance, release-level architecture/deployment/testing indexes, release notes/readiness, ignore/CI/package metadata, Portal documentation/port consistency, and Docker cleanup safety. The cleanup guard also extends the pre-existing changes in `deployment_cli.py`, `deployment_render.py`, and their tests; reviewers must assess those combined diffs rather than assuming file-level ownership.

## C. Security and release hygiene

- Actual Docker inspection found the PostgreSQL image-declared anonymous data volume on the `provision` container. It had only an anonymous-volume label, while the persistent PostgreSQL volume had Compose project/volume labels. This proves label absence is not a retention guarantee.
- The provision job now uses `tmpfs`; destructive reset inventories all attached volumes, prints exact names/destinations, and requires an exact deployment-name confirmation. No destructive command was run during release preparation.
- Git/Docker ignores exclude credentials, keys, `.venv`, `node_modules`, caches, logs, databases, backups, generated Compose, runtime state, and release output. Tracked-file and filename-history scans found zero non-example sensitive-path candidates.
- Secrets remain outside config and are create-only/restricted. Static high-confidence scans found zero tracked or archived files matching private-key, AWS access-key, GitHub-token, or Slack-token signatures. This is a scoped local scan, not an external secret-scanner attestation; no secret contents were printed or recorded.
- Project license is GPL-3.0-or-later. `THIRD_PARTY_LICENSES.md` and Portal notices exist, but the inventory is not a complete transitive/container SBOM. This is acceptable for the tested source-only pre-release with disclosure; prebuilt binary/container redistribution remains blocked pending complete notices/SBOM review.

## D. Test evidence

Executed during this release-preparation task:

| Check | Result |
|---|---|
| Deployment CLI/config/acceptance regressions | PASS — 57 tests |
| Authentication suite outside restricted Windows Proactor sandbox | PASS — 17 tests |
| Default non-integration/e2e pytest suite | PASS — 894; 71 deselected |
| Portal TypeScript + Vite production build | PASS |
| Portal Vitest | PASS — 9 tests |
| Portal Playwright desktop/mobile smoke | PASS — 15 tests after correcting one stale fixture-label assertion |
| Ruff lint excluding pre-existing unrelated `check_tang1.py` | PASS |
| Ruff format for release-touched Python | PASS; full-repository baseline still has unrelated legacy differences |
| Markdown local links | PASS |
| Source archive build/verifier | PASS — `flowbridge-0.1.0b1.tar.gz`, no private/generated paths or high-confidence secret hits |
| `git diff --check` | PASS |

The earlier ASGI timeout was reproduced with a 20-second bound. A faulthandler trace showed Windows Proactor event-loop creation blocked in `_fallback_socketpair` before the test fixture. The same 17 authentication tests passed outside the restricted sandbox; no application-code defect was found.

Prior cold-install evidence, not rerun in this task: Doctor/Smoke PASS, acceptance 8/8 three times, stop/start persistence PASS, setup rerun PASS, and the then-current 55 deployment tests PASS. See [fresh-install verification](../deployment/FRESH_INSTALL_VERIFICATION.md). No new live acceptance was run because the current deployment was not changed or designated for release-task writes.

## E. Remaining risks

### Release blockers

- **Prebuilt binary/container publication:** blocked until a complete dependency/container SBOM and applicable bundled notices are reviewed.
- **Production-ready claim:** blocked by unverified TLS/ingress, backup restore, upgrade/rollback, observability, and Linux acceptance.

These do not block a clearly marked GitHub **source pre-release**.

### Non-blocking issues for source pre-release

- Portal/Data Workspace are experimental and Portal live contracts are not connected to Layer 1 Core.
- Standalone installer and encrypted restore are not release-accepted.
- Repository-wide Ruff format has legacy baseline differences outside this release's touched Python files.
- Git history received a filename/high-confidence scan, not a full external secret-scanner attestation.

### Future improvements

- Add CI for full release checks, Markdown links, archive verification, SBOM/license generation, and platform-specific fresh install.
- Add production operational profiles and recovery/upgrade drills.
- Connect Layer 2 only through versioned, supported Layer 1 contracts.

## F. Release instructions after approval

1. Review `git status`, the complete diff, and this risk classification; exclude unrelated `check_tang1.py` unless separately approved.
2. Run final checks and commit all intended source/docs without generated runtime files.
3. Verify the final commit is clean and rerun archive creation from that commit.
4. Create annotated tag `v0.1.0-beta.1` on the verified commit.
5. Build and verify the source archive from that tag; record SHA-256 externally with the release assets.
6. Push the reviewed branch and annotated tag.
7. Create a GitHub **Pre-release** using `RELEASE_NOTES_v0.1.0-beta.1.md`; attach only verified source artifacts.
8. Download the published artifact, re-run verification, and test all documentation/download links.

No commit, tag, push, or GitHub release was performed by this preparation task.
