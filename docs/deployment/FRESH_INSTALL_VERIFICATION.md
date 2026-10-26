# FlowBridge Layer 1 fresh-install verification

## Result

FlowBridge Layer 1 was installed from an independent source snapshot after removal of all Docker images, all verified FlowBridge containers/networks/volumes, generated deployment state, and old credentials. The second cold-install attempt completed successfully after fixing a Windows log-decoding defect in the installer.

Verification completed on 2026-10-26 (Asia/Saigon). Source: branch `hoanganhdev_features`, base commit `b24275a26ed7737d4476948019f63d2a0787a2be`, plus the recorded uncommitted Layer 1 changes and fixes below. No commit or push was made.

## A. Environment cleanup

### Inventory and source protection

- Initial working tree: 23 changed/untracked paths before this report and its regression test were added.
- Initial Docker state: one `flowbridge` project, eight FlowBridge containers, one labeled PostgreSQL volume, one FlowBridge network, 82 images, and 558 BuildKit cache records.
- Independent snapshot: a sibling `FlowBridge-fresh-install-20261026` directory outside the checkout, created from 473 tracked/unignored files.
- The initial snapshot contained no `.git`, `.venv`, `node_modules`, `.tmp`, `__pycache__`, generated deployment directory, credentials, or ignored runtime state.
- Dependency manifests, lockfile, migrations, CLI, templates, Dockerfile, bootstrap scripts, tests, and docs were present.
- Git history and the original checkout were preserved.

### Removed

- Eight FlowBridge containers, network `flowbridge_internal`, volume `flowbridge_flowbridge_postgres`, all 82 images, all 547 reclaimable cache records, generated artifacts, and old/failed-attempt credentials.
- Docker reached zero images, zero containers, and zero volumes before the successful attempt.
- Eleven Docker-driver cache records (209.6 MB) remained `reclaimable=false`; both builder and buildx prune refused them. They were Docker Desktop internal active records, not reusable FlowBridge images.

### Safety exception

The approved anonymous volume `436ec61757471124ce1411a72106fe6c15f8157155a00ad81ae884a6d628b62b` was unexpectedly removed by `docker compose down --volumes`. It had no Compose label but was an anonymous mount attached to a FlowBridge container, so Compose treated it as project runtime state. It cannot be recovered and was not recreated artificially. Docker default networks were preserved; no unrelated container or Compose project existed.

## B. Fresh installation

### Host and prerequisites

- Windows 11 Pro 64-bit 10.0.26300.
- Docker client/server 29.8.2; Compose 5.6.0.
- `uv` 0.13.1; fresh CPython 3.12.13 environment.
- Docker Desktop with WSL2; services bind to `127.0.0.1`.

A new uv cache was used. `uv sync --frozen --group database --group dev` downloaded and installed 46 locked packages into a new `.venv`.

The initializer used documented Quick defaults: `flowbridge`, development, all Layer 1 services, `127.0.0.1`, and installer-generated random secrets. The executor lacks a Windows console screen buffer, so a UI adapter supplied these exact wizard answers; validation, secret generation, ACL handling, and file creation remained installer-owned.

### Cold installation

Attempt 1 reached migration but failed when `subprocess.run(text=True)` decoded Docker UTF-8 output as CP1252. The partial deployment, images, volume, generated files, and credentials were deleted. Docker was reset to zero containers/images/volumes.

After the UTF-8 fix, attempt 2 completed preflight, rendering, PostgreSQL startup, provisioning, migrations, service startup, Keycloak bootstrap, and readiness.

Created databases: `dxos_core_db`, `sales_db`, `warehouse_db`, `logistics_db`, `keycloak_db`, `hasura_metadata_db`, and `flowable_db`.

| Service | Binding | Result |
|---|---:|---|
| PostgreSQL | 127.0.0.1:55432 | healthy |
| Keycloak | 127.0.0.1:18443 | healthy |
| Hasura | 127.0.0.1:18080 | healthy |
| Flowable REST | 127.0.0.1:18083 | ready; authenticated check passed |
| Core API | 127.0.0.1:18084 | healthy |
| Core Worker | internal | running |
| Provision / Migrate | one-shot | exited 0 |

Only one Compose project exists: `flowbridge`.

## C. Verification results

| Verification | Result |
|---|---|
| Fresh locked dependency sync | PASS |
| Docker pull/build/provision/migrate | PASS |
| Doctor and smoke before restart | PASS |
| Acceptance run 1 | 8/8 PASS |
| Immediate acceptance run 2 | 8/8 PASS |
| Acceptance after stop/start | 8/8 PASS |
| Stop then start | PASS |
| Core persistence | PASS: 3 applications before and after |
| Setup rerun unchanged | PASS |
| Smoke after setup rerun | PASS |
| Installer/acceptance regression suite | PASS: 55 tests |
| Ruff lint/format | PASS |
| Active acceptance principals/memberships/grants | 0 / 0 / 0 |

Every acceptance run inserted and verified real rows: 38 Sales, 54 Warehouse, and 22 Logistics; each run then removed those unchanged rows. All runs passed `keycloak_core`, `no_token`, `invalid_token`, `expired_token`, `hasura_three_sources`, `grant_read_revoke`, `header_escalation`, and `acceptance_identities_disabled`. Hasura queried real Warehouse rows through governed access, not only `/healthz`.

A broader platform-test invocation progressed through 55 tests and then hung before the first ASGI authentication fixture under the restricted executor; it was stopped and is not reported as PASS for this cold-install run. Release-candidate follow-up isolated the stack in Windows Proactor event-loop creation under the sandbox, then passed all 17 authentication tests and the full default suite outside that restriction. Required real Keycloak/Core auth, invalid/expired token, Flowable Basic Auth, doctor, smoke, and acceptance all completed successfully.

## D. Defects fixed

### Windows Docker-output decoding

- Cause: Docker emitted UTF-8 while Python inherited Windows CP1252; the reader thread failed and returned `stdout=None`.
- Fix: force UTF-8 with replacement for malformed bytes.
- Files: `scripts/deployment_cli.py`, `tests/platform/test_deployment_cli.py`.
- Regression verifies `encoding='utf-8'` and `errors='replace'`.

### Acceptance lifecycle

- Empty governed reads were caused by fixtures being an undocumented separate prerequisite.
- Repeat HTTP 409 was caused by a live test grant left by an interrupted run.
- Acceptance now inserts and verifies fixtures, cleans them in `finally`, revokes stale/test grants, revokes memberships, disables identities, and refuses modified deterministic fixture rows.
- Files: `scripts/deployment_acceptance.py`, `scripts/database/seed.py`, `tests/platform/test_deployment_acceptance.py`, `infra/deployment/README.md`.
- Regression: cleanup-on-failure test and two consecutive real 8/8 runs.

## E. Reproduction

```powershell
uv sync --frozen --group database --group dev
uv run python -m scripts.deployment_cli init --config deployment.yaml
uv run python -m scripts.deployment_cli setup --config deployment.yaml --non-interactive
uv run python -m scripts.deployment_cli doctor --config deployment.yaml
uv run python -m scripts.deployment_smoke --config deployment.yaml
uv run python -m scripts.deployment_acceptance --config deployment.yaml
```

Lifecycle:

```powershell
uv run python -m scripts.deployment_cli status --config deployment.yaml
uv run python -m scripts.deployment_cli stop --config deployment.yaml --non-interactive
uv run python -m scripts.deployment_cli start --config deployment.yaml --non-interactive
uv run python -m scripts.deployment_cli setup --config deployment.yaml --non-interactive
```

Acceptance owns fixture preparation, verification, grant/identity cleanup, and fixture removal. No manual SQL or password repair is required.

## Portability statement

This was a cold installation on the current Windows 11/Docker Desktop host, not a second physical machine or VM. New-machine confidence is based on source inspection plus this independent-snapshot run; it is not presented as a separate-machine test. Production still needs HTTPS, public DNS, an external TLS proxy, protected secrets, backups, and rotation procedures.
