# FlowBridge Layer 1 clean-rebuild audit

Audit time: 2026-10-25 (Asia/Ho_Chi_Minh)

## Repository state before cleanup

- Repository root: the active `FlowBridge` checkout (host-specific absolute path omitted).
- CodeGraph is not enabled in this repository.
- Existing tracked modifications before the clean rebuild: `infra/postgres/bootstrap.sql`, `scripts/database/local.py`, and `scripts/docker_env.py`. These are startup fixes made during the preceding local-integration attempt and are preserved for review.
- Existing untracked file not created by this rebuild: `check_tang1.py`. It is preserved unchanged.
- `deployment.yaml`, `scripts/deployment_cli.py`, `scripts/deployment_smoke.py`, and the generated deployment stack exist.

## Docker inventory

Ownership was established from Docker Compose labels (`com.docker.compose.project`, service, config file and working-directory labels), not from name prefixes alone.

| Compose project | Containers | Running | Named volumes | Classification |
| --- | ---: | ---: | ---: | --- |
| `flowbridge` | 5 | 4 | 1 | Current official deployment; remove and recreate from a blank database |
| `flowbridge-b2-0812d8f4` | 5 | 0 | 1 | Repository test deployment |
| `flowbridge-b2-31eae629` | 5 | 0 | 1 | Repository test deployment |
| `flowbridge-b2-f090c0e1` | 5 | 0 | 1 | Repository test deployment |
| `flowbridge-b2-f413cace` | 5 | 0 | 1 | Repository test deployment |
| `flowbridge-c31-588ab366` | 5 | 0 | 1 | Repository test deployment |
| `flowbridge-c31q-95a797e0` | 5 | 0 | 1 | Repository test deployment |
| `flowbridge-chat` | 11 | 0 | 6 | Legacy FlowBridge chat test/development deployment |
| `flowbridge-dev` | 4 | 0 | 3 | Legacy FlowBridge development deployment |
| `flowbridge-integration` | 1 | 0 | 1 | Legacy Keycloak integration deployment |
| `flowbridge-local` | 2 | 0 | 1 | Legacy local PostgreSQL/Hasura deployment |
| `flowbridge-local-workflow` | 1 | 0 | 0 | Legacy Flowable deployment |

Each of the six `b2`/`c31` test projects also owns one anonymous PostgreSQL-image volume attached to its `provision` container. The current `flowbridge` provision container owns one equivalent anonymous volume. These mounts are confirmed by container inspection and are in cleanup scope. Every listed project except `flowbridge-local-workflow` owns a Compose-labelled network.

No non-FlowBridge Compose project, container, network, or volume is in scope.

## Persistent-data assessment

The current `flowbridge` PostgreSQL volume contains the seven designed databases plus `postgres`. Approximate application data before deletion:

- `dxos_core_db`, `sales_db`, `warehouse_db`, `logistics_db`: zero live rows.
- `keycloak_db`: 1,628 live rows in 33 system tables.
- `hasura_metadata_db`: 2 live rows in 2 system tables.
- `flowable_db`: 16 live rows in 5 system tables.

The non-empty rows are generated engine/bootstrap state and are reproducible from `deployment.yaml`, the secret input, migrations and bootstrap logic. The task explicitly authorizes deletion of confirmed FlowBridge test data and requires a fresh database, so no data backup is required. Legacy projects are labelled test/development/integration projects and are likewise reproducible; none is running.

## Approved cleanup set

Remove containers, Compose-labelled networks, named volumes and container-attached anonymous volumes for exactly these projects:

`flowbridge`, `flowbridge-b2-0812d8f4`, `flowbridge-b2-31eae629`, `flowbridge-b2-f090c0e1`, `flowbridge-b2-f413cace`, `flowbridge-c31-588ab366`, `flowbridge-c31q-95a797e0`, `flowbridge-chat`, `flowbridge-dev`, `flowbridge-integration`, `flowbridge-local`, and `flowbridge-local-workflow`.

Generated deployment artifacts under `infra/deployment/generated/` may be regenerated and are also in cleanup scope. Source, Git history, dependency definitions, `deployment.yaml`, secret input, and all Docker resources without verified FlowBridge Compose ownership are retained.

## Post-cleanup acceptance

- No legacy FlowBridge Compose project/resource remains.
- The only recreated project is `flowbridge`.
- PostgreSQL starts from the blank official volume and provisions all seven databases and least-privilege roles.
- Readiness and real authentication/integration checks must pass before completion is reported.

## Cleanup execution and final state

- Removed exactly 54 verified FlowBridge containers, 11 Compose-labelled networks and 25 volumes from the approved projects. No global prune command was used and no unrelated Docker resource was removed.
- Deleted only reproducible contents of `infra/deployment/generated/`; retained source, Git history, `deployment.yaml`, `deployment.state.json`, `secrets/deployment.env` and the pre-existing untracked `check_tang1.py`.
- Rebuilt a blank `flowbridge` project. The only persistent volume is the official PostgreSQL volume; it contains Core, Sales, Warehouse, Logistics, Keycloak, Hasura Metadata and Flowable databases.
- Final running services: PostgreSQL, Keycloak, Hasura, Flowable, Core API and Core Worker. Provision and migrate are successful one-shot jobs.
- Fresh setup and an unchanged second setup passed. `stop` then `start` preserved the checked Warehouse row count (`3` before and after).
- Real acceptance passed for Keycloak to Core authentication, invalid-token denial, three Hasura sources, consistent metadata, governed read, header-escalation denial and grant revocation.
- Flowable authenticated readiness returned HTTP 200 with configured credentials and HTTP 401 with an incorrect password. OIDC discovery returned HTTP 200.
- After acceptance, all three test service principals were disabled, their membership was revoked, the grant was revoked, and exactly 38 Sales, 54 Warehouse and 22 Logistics deterministic fixture rows were removed. Hasura source/track metadata remains as the verified Layer 1 configuration.
