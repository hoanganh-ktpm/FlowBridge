# Deployment configuration (Phase A)

## Scope after Layer 2 integration

The installer manages Layer 1 engines: PostgreSQL, Keycloak, Hasura and Flowable,
with the seven databases listed in `deployment.example.yaml`. It does not start
Core/worker, Business APIs, Data Workspace or Portal. Data Workspace remains a
separate native service with its own database and migration history; its setup
and start commands are documented in [Data Workspace](DATA-WORKSPACE.md).
The local Workspace helper targets the existing local development profile in
`.tmp/local-postgres`; it does not consume the installer's `deployment.yaml`.
The installer's backup/restore commands cover its seven configured databases;
they do not cover a separately created Workspace database.

## Configuration

Copy `deployment.example.yaml` to `deployment.yaml` and customize the name, environment, domain, published host ports, database names/user, admin user names, URLs, TLS, and named volumes. `database.names` lists Core, Sales, Warehouse, Logistics, Hasura metadata, Flowable, and Keycloak databases. `database.role_password_env` supplies separate owner/reader credentials without placing values in YAML. The old single `database.name` remains accepted for Phase A validation only; Phase B rendering requires `database.names`. Legacy PostgreSQL bootstrap is unchanged.

Keep `public_url` (browser-facing) distinct from `internal_url` (Docker network). By default, an internal URL must use the matching Docker service name; set `external: true` on that service only when its internal endpoint is externally managed. `identity.issuer` must equal the public Keycloak URL plus `/realms/<realm>`. For Flowable, `port` is the host-published port, `container_port` is the listener port, and `internal_url` ends in `/flowable-rest/service`. Enabled Keycloak, Hasura, and Flowable currently require enabled PostgreSQL.

Put the referenced secret variables in the ignored `.env` file or process environment. Existing process variables take precedence; validation never generates or replaces secrets. Do not commit `deployment.yaml`, `.env`, or `secrets/`. A minimal `.env` needs `POSTGRES_PASSWORD`, `KEYCLOAK_ADMIN_PASSWORD`, `HASURA_ADMIN_SECRET`, `FLOWABLE_ADMIN_PASSWORD`, and `OIDC_CLIENT_SECRET` when all services are enabled. Use distinct strong values outside development.

Validate without Docker: `python -m scripts.deployment_config --config deployment.yaml --env .env`. The example is a development template with reserved example domains and no credentials; it is not a deployable production configuration. For production, set TLS enabled, configure certificate termination (`external` or existing `files`), use HTTPS public URLs, real DNS, persistent volumes, and provision secrets securely. Validation checks structure and local inputs only; it does not deploy services or verify remote identity settings. Phase B must consume this config when generating/launching the deployment.

## Clean source installation (C3)

### Standalone Windows installer (F2)

Build on Windows with `python -m scripts.build_deployment_installer` after installing PyInstaller 6.22.3 into the build environment. The result is `dist/flowbridge-setup.exe` plus `dist/flowbridge-setup.exe.sha256`. On another Windows machine, copy only the executable, verify its SHA-256, ensure Docker Engine/Compose is running, then run `./flowbridge-setup.exe` to open the wizard. No Python, uv or repository checkout is needed there. By default, config and secrets live under `%LOCALAPPDATA%/FlowBridge/deployments/default/`, not beside the executable. Use `--config <path>` for another deployment; `status`, `start`, `stop`, `validate` and `doctor` are subcommands. The existing `flowbridge` Core entrypoint is unchanged. Build Linux separately on Linux; the Windows binary cannot run there. Encrypted backup/restore still requires separate `age` acceptance before release.

Use Python 3.12, uv 0.13.1, Docker Desktop/Engine with Compose, at least 4 GiB Docker memory and 5 GiB free disk. On Windows, open PowerShell in the extracted source directory:

```powershell
uv sync --frozen
uv run python -m scripts.deployment_cli setup
uv run python -m scripts.deployment_cli validate --config deployment.yaml
uv run python -m scripts.deployment_smoke --config deployment.yaml
uv run python -m scripts.deployment_cli status --config deployment.yaml
uv run python -m scripts.deployment_cli stop --config deployment.yaml
uv run python -m scripts.deployment_cli start --config deployment.yaml
```

The wizard creates ignored `deployment.yaml` and `secrets/deployment.env`, asks only relevant Quick/Advanced questions and checks ports before startup. The Docker network is internal; published ports bind to loopback. If a port is occupied, choose a suggested free port or enter another. If startup later races with another process, retry after changing the port with **Edit**. The setup error names the failed step and category; inspect private `infra/deployment/generated/setup.log` for filtered diagnostics. Never post this log without reviewing it. `setup --non-interactive --config deployment.yaml` never prompts; `--dry-run` validates without Docker writes. On Linux, use the same commands in a shell. Run `stop` and `start` with the same `--config` to restart without removing volumes. The `flowbridge` console command remains reserved for Core; use the Python module for deployment.

Production/staging need public DNS, an external TLS reverse proxy and manually prepared Flowable schema before setup; no development defaults are silently accepted. External PostgreSQL, automated upgrades and rollback are **UNSUPPORTED**. Before any manual image/schema upgrade, take an encrypted backup, verify image/schema compatibility in a separate test deployment, then schedule the upgrade; never point a new image at production volumes speculatively.

### Backup/restore safety

All persistent application state (Keycloak realm, Hasura metadata and Flowable data) resides in the seven PostgreSQL databases. Keep `deployment.yaml` and `secrets/deployment.env` in a separate secure secret store; never add them to an unencrypted archive. `python -m scripts.deployment_backup backup --config deployment.yaml --output <private-directory> --recipient <age-recipient>` streams all seven database dumps directly into `age`-encrypted files and records pinned image versions; it refuses to overwrite an existing destination. Restore requires `age`, a distinct development project and volumes, empty provisioned databases, an identity file, and exact `--confirm-target <deployment-name>`; run `python -m scripts.deployment_backup restore --config <test-deployment.yaml> --archive <private-directory> --identity <private-key-file> --confirm-target <test-deployment-name>`. Do not restore into production or an existing project. The `age` tool is absent on the current host, so encrypted backup and restore are **BLOCKED/NOT RUN**, not a verified recovery procedure.

### Source archive and fresh-machine checklist

```powershell
uv build --sdist
$archive = (Get-ChildItem dist\flowbridge-*.tar.gz | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
uv run python -m scripts.verify_source_archive $archive
uv run python -m scripts.create_source_archive $archive dist\flowbridge-clean.zip
uv run python -m scripts.verify_source_archive dist\flowbridge-clean.zip
```

Share the archive only after the verifier reports PASS. The archive includes deployment scripts, lockfile, example, tests and license; it must not include `.git`, `.venv`, `node_modules`, `.tmp`, secrets, certificates or generated Compose. On a new Windows/Linux machine, extract it, run `uv sync --frozen`, then the setup and smoke commands above. Check that Docker daemon/Compose are available, port ownership is clear, `secrets/deployment.env` is private, services are healthy, and the browser-facing Keycloak/Hasura/Flowable URLs are reachable through the configured TLS proxy. Linux installation and full clean-machine JWT acceptance remain **NOT RUN** unless performed on that host.
