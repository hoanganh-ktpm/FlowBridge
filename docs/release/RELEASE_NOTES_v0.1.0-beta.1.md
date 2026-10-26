# FlowBridge v0.1.0-beta.1

FlowBridge is a data-interoperability and business-process orchestration platform for connecting independent systems without replacing them. This first beta publishes the reusable **Layer 1 FlowBridge Core** and its reproducible local deployment.

## Included in this beta

- Application onboarding and registry.
- PostgreSQL source registration and schema discovery.
- Versioned data contracts and data-product publication.
- Keycloak OIDC/JWT authentication plus Core authorization policy.
- Access request, approval, scoped grant, governed read, expiry, and revoke.
- Hasura source/metadata/column-permission reconciliation by Core Worker.
- Allow-listed Flowable workflow/action integration.
- Seven PostgreSQL databases with migrations and least-privilege roles.
- Source installer, secret initialization, doctor, smoke, real acceptance, and archive verification.

PostgreSQL, Keycloak, Hasura, and Flowable are integrated third-party engines. FlowBridge provides the shared domain model, policy, lifecycle, provisioning, and operational controls around them.

## Requirements and installation

The accepted environment was Windows 11 with Docker Desktop Linux containers, Docker Engine 29.8.2, Compose 5.6.0, uv 0.13.1, and CPython 3.12.13. Allocate at least 4 GiB to Docker and keep 5 GiB free.

```powershell
uv sync --frozen --group database
uv run python -m scripts.deployment_cli init --config deployment.yaml
uv run python -m scripts.deployment_cli setup --config deployment.yaml --non-interactive
uv run python -m scripts.deployment_cli doctor --config deployment.yaml
uv run python -m scripts.deployment_smoke --config deployment.yaml
```

The initializer creates local ignored configuration and new secrets. Do not reuse credentials from another deployment. Read [Deployment](../DEPLOYMENT.md) before using staging/production settings.

## Verification evidence

Cold-install acceptance on 2026-10-26 completed: zero-image rebuild, seven-database provisioning, migrations, Keycloak bootstrap, all Layer 1 services, doctor and smoke, three acceptance runs at 8/8, immediate rerun, stop/start persistence, and setup rerun. Release-candidate regression evidence is recorded separately in [Release readiness](RELEASE_READINESS_v0.1.0-beta.1.md).

## Important safety fix

Docker Compose can remove an anonymous volume attached to a project container even when that volume has no Compose project label. The provision job now overrides the PostgreSQL image's implicit data volume with `tmpfs`. The guarded reset command prints attached named and anonymous volumes and requires `--confirm-reset <exact-deployment-name>` before deletion. No deleted historical volume was recreated.

## Known limitations

- Pre-release software; do not expose the default development topology directly to untrusted networks.
- Linux fresh-install acceptance, production ingress/TLS, external PostgreSQL, automated upgrades/rollback, and disaster-recovery restore are not verified.
- Portal UI and Data Workspace are experimental. The Portal's live legacy Agent Hub/purchasing endpoints are not supplied by Layer 1 Core, so Portal-to-Core SSO/data integration is not a beta claim.
- Acceptance writes controlled fixtures and is restricted to an authorized isolated development deployment.
- The third-party inventory is scoped, not a complete transitive/container SBOM or binary redistribution notice bundle.
- A small legacy formatting baseline remains outside the release-change set; see the readiness report.

## Next direction

Next work should harden production operations (TLS/ingress, secrets, monitoring, backup/restore and upgrades), complete dependency/container SBOM and notices, connect a stable Layer 2 client to supported Core contracts, and independently accept Linux deployment.

This is a source release candidate under [GPL-3.0-or-later](../../LICENSE). It is not yet tagged or published.
