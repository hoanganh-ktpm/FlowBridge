# Testing FlowBridge

Tests are separated by boundary. Mocked/unit results are not presented as proof that a live deployment works.

## Install test dependencies

```powershell
uv sync --frozen --group database --group dev
```

## Unit and regression tests

The default pytest configuration excludes tests marked `integration` or `e2e`:

```powershell
uv run --frozen pytest -q
uv run --frozen pytest tests/platform/test_deployment_cli.py tests/platform/test_deployment_config.py tests/platform/test_deployment_acceptance.py -q
```

Focused groups include `tests/core/`, `tests/platform/`, `tests/domain/`, and `tests/contracts/`. Contract tests validate OpenAPI/JSON Schema fixtures without running services. Runtime tests may use injected HTTP/repository doubles; their PASS does not prove Keycloak, Hasura, Flowable, or PostgreSQL integration.

## Database integration and E2E

Integration tests require an explicitly designated test database and the `database` dependency group. Never point them at production or an unapproved shared database.

```powershell
$env:DXOS_TEST_ADMIN_URL = 'postgresql+psycopg://.../postgres'
uv run --frozen --group database pytest tests/e2e/database -m "integration and e2e" -q
```

The test suite creates schemas/rows and may reset its own test resources. Review each suite's environment variables before opting in. Windows contributors can use `scripts/test_postgres.ps1` for the isolated local PostgreSQL test profile.

## Deployment doctor and smoke

These checks are intended to be non-mutating with respect to application records:

```powershell
uv run python -m scripts.deployment_cli doctor --config deployment.yaml
uv run python -m scripts.deployment_smoke --config deployment.yaml
```

`doctor` checks config, Docker preflight, Compose, and readiness. Smoke checks HTTP reachability and expected authentication responses. PASS means those checks succeeded at that moment; it is not a substitute for acceptance or production monitoring.

## Real acceptance test

Only run acceptance against an isolated development deployment where writes are authorized:

```powershell
uv run python -m scripts.deployment_acceptance --config deployment.yaml
```

The runner:

1. Seeds deterministic test rows in the Sales, Warehouse, and Logistics databases.
2. Obtains real Keycloak client-credential tokens and verifies missing, malformed, expired, and valid-token behavior.
3. Verifies three worker-managed Hasura sources and consistent metadata.
4. Exercises request/approve, scoped read, column projection, caller-header escalation denial, and revoke.
5. Disables isolated test identities, revokes stale test grants, and deletes only unchanged fixture IDs in `finally` cleanup.

A failure can still leave evidence requiring operator review if the process or host is terminated before cleanup. Rerun against the same isolated deployment to perform stale-grant cleanup, then inspect records before disposal. Acceptance is disabled outside `development` configuration.

## Cleanup and PASS/FAIL meaning

- Unit PASS: asserted code paths passed in the current process, often with doubles.
- Integration PASS: the named external boundary and configuration were exercised.
- Acceptance `8/8 PASS`: all eight live Layer 1 scenarios passed and the runner completed cleanup.
- SKIP/NOT RUN: no evidence for that scenario; do not reinterpret it as PASS.
- A timeout or interrupted suite is FAIL/INCOMPLETE until its root cause is resolved or disclosed.

Run `uv run ruff check .`, `uv run ruff format --check .`, archive verification, Markdown-link checks, and `git diff --check` for a release candidate.
