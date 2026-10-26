# Security policy

## Supported versions

Security fixes are evaluated for the current beta line, `v0.1.0-beta.x`. Pre-release software may change without backward-compatibility guarantees. Older development snapshots are unsupported.

## Reporting a vulnerability

Do not open a public issue containing vulnerability details, credentials, personal data, or an exploit. Use GitHub's private vulnerability reporting feature for this repository when enabled. If it is not available, contact the maintainers through a private channel listed on the repository profile and request a secure reporting route without sending sensitive details first.

Include the affected version/commit, component, reproduction conditions, impact, and any suggested mitigation. Do not test against systems or data you do not own or have explicit permission to use. Maintainers will acknowledge the report, assess severity and affected versions, coordinate a fix/disclosure plan, and credit reporters who want attribution.

## Layer 1 security model

- Keycloak issues OIDC/JWT identities. Core validates signature, issuer, audience, time claims, and subject mapping before domain authorization.
- Core policies authorize onboarding, publication, access approval, grant, revoke, and reads. Client-supplied role or identity headers are not trusted as proof of authorization.
- Hasura enforces generated role/column permissions. Its admin secret is infrastructure-only and must never reach browsers or business clients.
- PostgreSQL uses separate database owners and reader roles; provisioning checks that managed roles are not superusers and cannot replicate or bypass row security.
- Flowable actions and workflows are allow-listed. External action hosts and credentials require operator review.
- Development ports bind to loopback; the Compose network is private. This does not replace a production firewall or reverse proxy.

## Secrets and operations

Keep `deployment.yaml`, `secrets/deployment.env`, generated Compose/env files, logs, backups, private keys, and database dumps outside Git and unencrypted archives. Use unique secrets per environment and a managed secret store in production. Rotate any credential that may have entered Git history, logs, chat, CI output, or an artifact; deleting the latest file does not remove it from history.

Before a release, scan tracked files and history with an approved secret scanner, verify the source archive, review container image provenance, and produce a complete dependency/container SBOM and license-notice bundle. The repository's current third-party inventory is scoped and must not be treated as a full compliance certification.

## Production hardening still required

The beta is not production-ready by default. Operators must add external TLS/DNS, secure headers and ingress limits, centralized audit/monitoring, rate limiting, backup/restore drills, vulnerability/image scanning, dependency update policy, certificate and secret rotation, restricted Docker/database administration, and a tested upgrade/rollback procedure. Review [Deployment](docs/DEPLOYMENT.md) and [release readiness](docs/release/RELEASE_READINESS_v0.1.0-beta.1.md) before exposure beyond a controlled environment.
