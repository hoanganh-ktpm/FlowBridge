# Contributing to FlowBridge

Thank you for helping improve FlowBridge. Keep changes focused, preserve system boundaries, and state exactly which tests were run.

## Development setup

Requirements are Python 3.12, uv 0.13.1, and Docker/Compose when working on integration or deployment code.

```powershell
uv sync --frozen --group database --group dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

The default pytest selection excludes `integration` and `e2e`. Opt in only with explicitly configured test infrastructure; see [tests/README.md](tests/README.md). Portal development has a separate locked Node environment documented in [portal/README.md](portal/README.md).

## Change workflow

1. Open or reference an issue that describes the user impact and boundary.
2. Create a focused branch; do not mix generated files or unrelated refactors.
3. Add regression tests for behavior changes and update authoritative documentation.
4. Run the narrow tests first, then the broader non-integration suite where practical.
5. Run lint, format checks, Markdown-link checks, and `git diff --check`.
6. In the pull request, list commands/results, integration requirements, migrations, security implications, and remaining limitations.

Do not commit `.env` files, `deployment.yaml`, `secrets/`, generated Compose, runtime state, logs, databases, keys, `.venv`, `node_modules`, or test output. Never use real credentials in fixtures. Migrations must be additive and reviewed for rollback/operational impact.

## Architecture and ownership

- `backend/app/core/` owns FlowBridge domain behavior and orchestration.
- `backend/app/platform/` owns shared adapters and boundary utilities.
- `services/` owns example business APIs and the experimental workspace service.
- `infra/` and `scripts/` own reproducible deployment and operations.
- Third-party engine behavior must be attributed to PostgreSQL, Keycloak, Hasura, or Flowable rather than represented as native FlowBridge functionality.

Preserve authentication/authorization boundaries, least-privilege database roles, idempotency, and secret redaction. Do not weaken a guard merely to make a test pass.

## Pull requests and reports

Use clear commits once the change has been reviewed locally. Maintainers may request release notes, schema evidence, or a fresh-install rerun for deployment changes. Report security vulnerabilities privately according to [SECURITY.md](SECURITY.md); do not publish exploit details in a public issue.

Contributions are accepted under the repository's [GPL-3.0-or-later license](LICENSE).
