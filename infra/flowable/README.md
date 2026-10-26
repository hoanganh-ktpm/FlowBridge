<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Flowable Integration Runtime

> The official Layer 1 beta runs Flowable, Core API, and Core Worker in the generated stack documented in [Layer 1 deployment](../deployment/README.md). The profiles below are preserved for isolated workflow development and historical acceptance only.

## Standalone local business-workflow profile

Use `docker-compose.local.yml` for the verified local Business Workflow. It runs Flowable OSS
7.2.0 on port **18083**, with its own `flowable_db` in the existing local PostgreSQL 16 container.
It does not use the legacy Supabase profile below. Configuration and acceptance evidence:
[Local Business Workflow](../../docs/WORKFLOW.md#local-business-workflow--2026-10-24).

```powershell
.venv/Scripts/python.exe -m scripts.business_environment configure
.venv/Scripts/python.exe -m scripts.business_environment up
```

Keep the generated ignored `.tmp/business-workflow` secrets for subsequent runs.

## Historical Supabase Profile

The following documents the earlier acceptance environment. Do not use it for the local
Business Workflow or reconnect it to inspect local tests.

Flowable REST **7.2.0**, pinned by image digest. This standalone Compose starts only Flowable;
Core API and Publication Worker remain native Windows Python processes. Keycloak and Hasura
retain their existing integration profile. No PostgreSQL container or database is created.

## Storage And TLS

- Existing Supabase `postgres` database, dedicated `flowable_runtime` schema and login role.
- The engine creates/updates its own tables; it has no CREATE privilege on `public` and no Core/business credentials.
- JDBC uses the session/direct connection, not the transaction pooler. Connection pool maximum: 5.
- Preferred TLS: `verify-full` with the project's CA mounted read-only at `/run/tls/postgres-ca.pem`.
- The current acceptance run explicitly permits **test-only `sslmode=require`**: encrypted transport without full server identity verification. Do not treat that exception as production configuration or apply it to other modules.

Bootstrap is a separate, explicitly approved operation, never an application startup action:

```powershell
.venv/Scripts/python.exe scripts/workflow_environment.py --approve-flowable-schema-role --ca-file C:/path/to/project-ca.pem
```

For the authorized temporary exception, replace `--ca-file ...` with `--allow-unverified-tls-for-test`.
The bootstrap refuses existing schema/role/config ownership changes; retain the generated ignored
`.tmp/flowable-integration/docker.env` rather than rotating credentials on subsequent runs.

## Start And Restart

```powershell
docker compose --env-file .tmp/flowable-integration/docker.env -f infra/flowable/docker-compose.flowable.yml up -d
docker compose --env-file .tmp/flowable-integration/docker.env -f infra/flowable/docker-compose.flowable.yml ps
docker compose --env-file .tmp/flowable-integration/docker.env -f infra/flowable/docker-compose.flowable.yml restart flowable
```

REST base: `http://127.0.0.1:18081/flowable-rest/service`; Basic authentication is required.
Healthcheck uses the authenticated engine endpoint. Demo definitions and Java serialized variables
are disabled. Credentials, CA files, tokens and runtime logs stay outside Git.

## Native Callback Boundary

BPMN HTTP tasks reach the native API through `host.docker.internal:18000`. The integration harness
temporarily binds the native API to `0.0.0.0` for container access and stops only the processes it
starts. Keep the host firewall restrictive; this is not a public production deployment.

`FLOWBRIDGE_WORKFLOW_BASE_URL` and `FLOWBRIDGE_WORKFLOW_ENGINE_TOKEN` are engine environment
configuration. Use `requestSecureHeaders`, disable `saveRequestVariables`, and do not put access
tokens in process variables. Custom HTTP actions are registered in Core, not built into the image.

Commands, acceptance scope and limitations: [Workflow](../../docs/WORKFLOW.md).
