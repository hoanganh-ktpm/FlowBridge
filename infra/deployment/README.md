# FlowBridge Layer 1 deployment implementation

The generated topology contains PostgreSQL, provision/migrate jobs, Keycloak, Hasura, Flowable, Core API, and Core Worker. Public development ports bind to `127.0.0.1`; service traffic uses the private Compose network.

## Fresh installation

```powershell
uv sync --frozen --group database
uv run python -m scripts.deployment_cli init --config deployment.yaml
uv run python -m scripts.deployment_cli setup --config deployment.yaml --non-interactive
uv run python -m scripts.deployment_smoke --config deployment.yaml
```

The initializer writes ignored configuration/secrets. Setup renders ignored artifacts in this directory's `generated/` child, provisions seven databases/roles, applies four application migration histories, bootstraps Keycloak, and waits for readiness. It is designed to be rerun with unchanged secrets.

## Lifecycle and persistence

```powershell
uv run python -m scripts.deployment_cli status --config deployment.yaml
uv run python -m scripts.deployment_cli stop --config deployment.yaml
uv run python -m scripts.deployment_cli start --config deployment.yaml
uv run python -m scripts.deployment_cli doctor --config deployment.yaml
```

`stop` preserves containers and volumes. Keycloak, Hasura metadata, Flowable, Core, and example application state are stored in the declared PostgreSQL volume.

To uninstall containers/network while retaining database data, use Compose `down --remove-orphans` against the exact generated stack and project only after backup/review. Do not add `--volumes`.

For a disposable deployment, preview a destructive reset first:

```powershell
uv run python -m scripts.deployment_cli reset --config deployment.yaml --dry-run
```

The preview enumerates attached named and anonymous volumes. Docker may delete an anonymous volume even without a Compose project label. After verifying the inventory and backup, an operator can rerun with `--confirm-reset <exact-deployment-name>`. Never use global Docker prune commands.

The `provision` service overrides the PostgreSQL image's declared data directory with `tmpfs`; it is a stateless SQL client and must not create a disposable anonymous data volume.

## Acceptance

Only against an authorized isolated development deployment:

```powershell
uv run python -m scripts.deployment_acceptance --config deployment.yaml
```

It uses real databases, Keycloak tokens, Core, Worker, and Hasura. Controlled fixtures, stale test grants, and test identities are cleaned by the runner. See [testing guidance](../../tests/README.md).

## Secrets and production boundaries

Keep `secrets/deployment.env`, generated env files, and filtered logs private. Existing database passwords are never silently rotated by setup. Production requires HTTPS/DNS, an external reverse proxy, operator secret storage, observability, backup/restore drills, and a manual Flowable schema/upgrade plan. This installer does not automate production ingress, upgrades, rollback, or external PostgreSQL.

The authoritative operator guide is [docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md).
