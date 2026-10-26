# Changelog

All notable changes to FlowBridge are documented here. This project follows semantic-version intent; beta identifiers indicate a pre-release.

## [0.1.0-beta.1] - 2026-10-26

### Added

- Layer 1 Core API and Worker for application onboarding, data-source/product registration, versioned contracts, publication, governance, and governed reads.
- Seven-database PostgreSQL 16 topology for Core, Sales, Warehouse, Logistics, Keycloak, Hasura metadata, and Flowable.
- Keycloak realm/client bootstrap, JWT verification, Hasura metadata/permission reconciliation, and Flowable integration.
- Source installer with config validation, secret initialization, database provisioning/migrations, doctor, smoke, and real acceptance tooling.
- Acceptance fixture lifecycle for controlled seed, verify, grant/read/revoke, and cleanup.
- Source-archive verification and release documentation.

### Changed

- Core API and Core Worker now run in the generated Layer 1 Docker Compose stack.
- Release version normalized to `v0.1.0-beta.1` (`0.1.0b1` in Python package metadata).
- Installation and architecture documentation now distinguish the stable Layer 1 beta from experimental Portal/Data Workspace components.
- The root README is now available in Vietnamese and includes the Layer 1 architecture diagram plus an explicit Layer 2 development status.

### Fixed

- Docker command output is decoded as UTF-8 with replacement handling on Windows hosts.
- Repeated acceptance runs remove stale grants/identities and clean deterministic fixtures after failures.
- The stateless PostgreSQL provisioning job uses `tmpfs` for the image-declared data directory, preventing creation of a new unlabeled anonymous volume.
- Destructive reset inventories attached volumes and requires the exact deployment name before Compose can remove volumes.
- Portal development, Keycloak redirect, and Playwright configuration now consistently use port `5173`; a stale mobile-source assertion now follows the current fixture label.

### Security

- Deployment secrets remain outside YAML/Git and are create-only with restricted filesystem permissions.
- Database owner/reader roles, JWT validation, Hasura admin-secret boundaries, loopback port binding, and caller-header escalation rejection are covered by configuration or tests.
- Source archive verification rejects common secret, key, cache, generated-state, and runtime paths.

### Known limitations

- This beta is verified on Windows 11 with Docker Desktop; Linux fresh installation has not been independently accepted for this release.
- Production ingress/TLS automation, external PostgreSQL, automated upgrade/rollback, and disaster-recovery restore are not verified.
- Portal and Data Workspace remain experimental and are not started by the Layer 1 installer.
- The third-party license inventory is scoped and is not a complete transitive SBOM for redistributed container/binary artifacts.
- A broader platform authentication suite has previously timed out and requires explicit release-candidate evidence; see the release-readiness report.

## Unreleased

No unreleased changes are recorded after the first beta release candidate.
