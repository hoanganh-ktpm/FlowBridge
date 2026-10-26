# Layer 1 deployment

This document describes the `v0.1.0-beta.1` source-based deployment. The supported beta topology is one Docker Compose project with PostgreSQL 16, Keycloak, Hasura, Flowable, Core API, Core Worker, and one-shot provisioning/migration jobs.

## Prerequisites

- Python 3.12 (installed by uv from `.python-version`).
- uv `0.13.1`, enforced by `pyproject.toml`.
- Docker Engine and Docker Compose v2; Docker Desktop Linux containers were used for Windows verification.
- At least 4 GiB Docker memory and 5 GiB free disk.
- Loopback ports `55432`, `18443`, `18080`, `18083`, and `18084`, unless customized.

## Fresh installation

Run from an extracted source archive or repository root:

```powershell
uv sync --frozen --group database
uv run python -m scripts.deployment_cli init --config deployment.yaml
uv run python -m scripts.deployment_cli setup --config deployment.yaml --non-interactive
```

`init` is interactive. It writes ignored `deployment.yaml` plus `secrets/deployment.env`, generates strong values, and refuses to overwrite an existing secret file. `setup` validates ports and resources, renders ignored files under `infra/deployment/generated/`, pulls/builds images, provisions seven databases and scoped roles, applies migrations, bootstraps Keycloak, and waits for readiness. Rerunning setup with unchanged inputs is supported.

Do not copy credentials from another deployment to make installation pass. Do not commit `deployment.yaml`, `secrets/`, generated Compose files, or state files.

## Configuration

Start from `deployment.example.yaml`. Important fields are:

- `name`: Compose project/deployment identity.
- `environment`: `development`, `staging`, or `production`.
- `services.*.port`: loopback host port; change conflicts instead of stopping unrelated processes.
- `public_url`: browser/client-facing URL.
- `internal_url`: Docker service URL; it must not be confused with the public URL.
- `database.names` and `role_password_env`: seven databases and separate owner/reader credentials.
- `identity`: realm, issuer, API/browser clients, and redirect URIs.
- `tls`: external or file termination. Staging/production require HTTPS configuration.
- `volumes.postgres`: persistent PostgreSQL volume name.

Validate without changing Docker:

```powershell
uv run python -m scripts.deployment_cli validate --config deployment.yaml
uv run python -m scripts.deployment_cli setup --config deployment.yaml --non-interactive --dry-run
```

## Secrets

The initializer stores secrets in `secrets/deployment.env` with restricted permissions. Process environment variables take precedence where supported. Use unique, externally managed secrets for non-development deployments. Generated env files and `setup.log` are private; review logs before sharing even though known values are redacted.

Changing a database role password in the secret input does not rotate the existing PostgreSQL role. Rotation requires a backup, an authenticated database change, an atomic secret update, recreation of only affected services, and verification. `start` is not a rotation operation.

## Lifecycle

```powershell
uv run python -m scripts.deployment_cli status --config deployment.yaml
uv run python -m scripts.deployment_cli stop --config deployment.yaml
uv run python -m scripts.deployment_cli start --config deployment.yaml
uv run python -m scripts.deployment_cli doctor --config deployment.yaml
uv run python -m scripts.deployment_smoke --config deployment.yaml
```

- **Stop** uses `docker compose stop`; containers and all data remain.
- **Start/restart** recreates no database and retains the PostgreSQL volume.
- **Uninstall while retaining data** is an operator action: after backup and review, run Compose `down --remove-orphans` against the generated file without `--volumes`. Containers/network are removed; declared volumes remain.
- **Destructive reset** removes containers, the project network, and all attached/declared volumes. Use only for a disposable deployment as described below.

## Health and persistence

`doctor` validates configuration, Docker availability/resources/ports, Compose syntax, and deployed readiness. `deployment_smoke` checks service endpoints and expected authentication behavior without modifying application records. A healthy container alone is not full integration evidence; the real acceptance runner verifies token, publication, grant/read/revoke, and cleanup flows.

All durable Layer 1 state is in PostgreSQL: Core, example business databases, Keycloak, Hasura metadata, and Flowable. Back up all seven databases together with the matching configuration and protected secrets. The encrypted `age` backup/restore command exists, but recovery remains experimental until exercised on the target operator environment.

## Cleanup safety

Never use global Docker prune commands for FlowBridge. Docker images may declare their own anonymous volumes; those volumes can be attached to a Compose-owned container even though the volume itself has no Compose project label. Absence of a label is not evidence that `docker compose down --volumes` will preserve it.

The renderer mounts `tmpfs` over the PostgreSQL image's data directory for the stateless provision job to prevent new anonymous data volumes. Before an intentional reset, first print the exact attached-volume inventory:

```powershell
uv run python -m scripts.deployment_cli reset --config deployment.yaml --dry-run
```

After reviewing the list and a verified backup, repeat with the exact deployment name:

```powershell
uv run python -m scripts.deployment_cli reset --config deployment.yaml --confirm-reset flowbridge-example
```

The reset command refuses a missing/mismatched confirmation and reports declared as well as anonymous/unmodeled attached volumes. It does not infer safety from labels. This command is destructive and cannot restore a deleted volume.

## Troubleshooting

- **Port occupied:** edit that service's host port; do not terminate the existing owner automatically.
- **Docker unavailable/resource check:** start Docker Desktop/Engine and allocate the required memory/disk.
- **Password failure after prior install:** restore matching secrets or perform deliberate credential rotation; repeated setup will not silently rotate roles.
- **Keycloak:** verify realm bootstrap and issuer/public URL consistency.
- **Hasura:** `/healthz` is necessary but not sufficient; inspect metadata consistency and run acceptance in an isolated development deployment.
- **Flowable:** authenticated actuator access should succeed while incorrect credentials should fail.
- **Core:** inspect private Core API/Worker logs and confirm migrations, Keycloak, Hasura, and Flowable are ready.

## Environment limitations

The default development template binds published ports to loopback and uses development schema policies. Production/staging require real DNS, HTTPS through an external reverse proxy, production secret storage, observability, backup/restore drills, manually prepared Flowable schema, and explicit upgrade/rollback procedures. External PostgreSQL, automated upgrades, automated rollback, and production ingress are unsupported by this beta installer.

See [Testing](../tests/README.md), [Security](../SECURITY.md), and the [fresh-install evidence](deployment/FRESH_INSTALL_VERIFICATION.md).
