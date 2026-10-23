# Third-Party Licenses

## Hasura integration audit

Core v0.2 hardening review: rechecked installed/locked HTTPX 0.28.1 (BSD-3-Clause), Pydantic 2.14.1 (MIT), Pydantic Settings 2.16.0 (MIT). Added code uses only these existing packages and Python stdlib; no new dependency or engine artifact. Reading pinned Hasura source for API verification does not vendor that source. Runtime scope and integration checks are documented in [Docker Integration](docs/DOCKER.md).

Scope: components used directly by `backend/app/core/hasura_provisioner`. No dependency was added, upgraded or installed by this module. Existing Python versions remain pinned in `pyproject.toml` / `uv.lock`. This inventory does not replace the full distribution's transitive dependency notices or the [Portal inventory](portal/THIRD-PARTY-NOTICES.md).

| Component | Version / status | Official repository | License | Use |
|---|---|---|---|---|
| Hasura GraphQL Engine v2 CE | v2.48.16, pinned integration profile | [hasura/graphql-engine](https://github.com/hasura/graphql-engine/tree/v2.48.16) | Apache-2.0 root license; MIT community portions, according to upstream component notices | External GraphQL/Metadata engine; only CE Metadata API operations |
| HTTPX | 0.28.1, existing lock | [encode/httpx](https://github.com/encode/httpx/tree/0.28.1) | BSD-3-Clause | Injected async HTTP client and MockTransport |
| Pydantic | 2.14.1, existing lock | [pydantic/pydantic](https://github.com/pydantic/pydantic) | MIT | DTO validation and secret-safe representation |
| Pydantic Settings | 2.16.0, existing lock | [pydantic/pydantic-settings](https://github.com/pydantic/pydantic-settings) | MIT | Environment configuration |

Verified against official repository license files and the installed Python distribution LICENSE files/metadata. The pinned Hasura v2.48.16 root and community license files were fetched directly from upstream for this review. Python upstream license texts were checked against the locally installed pinned distributions; this is not a claim that a future/unavailable Git tag was verified. License verification is separate from integration/runtime compatibility testing.

### Upstream license evidence and redistribution requirements

- **Hasura:** [Apache-2.0](https://github.com/hasura/graphql-engine/blob/v2.48.16/LICENSE), [MIT community license](https://github.com/hasura/graphql-engine/blob/v2.48.16/LICENSE-community). Preserve upstream component headers, copyright, applicable license text and any NOTICE when redistributing binaries/source; review the exact CE build artifact before release. Community notice identifies Hasura Technologies Private Limited. Do not substitute an Enterprise artifact or assume all directories/images in a public repository have the same license. No Hasura binary or source is vendored here.
- **HTTPX:** [BSD-3-Clause](https://github.com/encode/httpx/blob/0.28.1/LICENSE.md). Copyright 2019 Encode OSS Ltd. Retain the copyright, three conditions and warranty/liability disclaimer in source redistributions; reproduce them in binary redistribution documentation. Do not use contributor names to endorse a product without permission. Installed distribution retains `httpx-0.28.1.dist-info/licenses/LICENSE.md`.
- **Pydantic:** [MIT](https://github.com/pydantic/pydantic/blob/main/LICENSE). Copyright 2017 to present Pydantic Services Inc. and individual contributors. Retain copyright and permission notice, including disclaimer, with copies/substantial portions. Installed pinned distribution retains `pydantic-2.14.1.dist-info/licenses/LICENSE`.
- **Pydantic Settings:** [MIT](https://github.com/pydantic/pydantic-settings/blob/main/LICENSE). Copyright 2022 Samuel Colvin and other contributors. Retain copyright and permission notice, including disclaimer, with copies/substantial portions. Installed pinned distribution retains `pydantic_settings-2.16.0.dist-info/licenses/LICENSE`.

Do not strip `.dist-info/licenses` from a packaged Python environment. A link/inventory alone is not a replacement for bundled license texts when redistributing the dependency itself. Existing pytest/pytest-asyncio/Ruff are development tools, not newly chosen runtime dependencies. SQLAlchemy/Alembic remain owned by Core DB and are not reimplemented or invoked here. No proprietary service, Hasura Cloud requirement or Enterprise-only feature is introduced.

## Declarative onboarding additions

The onboarding module reuses existing locked packages; no dependency/version is added. Existing Pydantic 2.14.1 (MIT), HTTPX 0.28.1 (BSD-3-Clause) and Pydantic Settings 2.16.0 (MIT) remain covered above. New direct runtime use:

| Component | Pinned version | Official repository / license | SPDX | Use / required notice |
|---|---|---|---|---|
| SQLAlchemy | 2.1.4 | [sqlalchemy/sqlalchemy](https://github.com/sqlalchemy/sqlalchemy), [LICENSE](https://github.com/sqlalchemy/sqlalchemy/blob/main/LICENSE) | MIT | Inspector/async engine. Retain SQLAlchemy authors/contributors copyright, permission and disclaimer; installed distribution contains LICENSE and AUTHORS |
| asyncpg | 0.32.0 | [MagicStack/asyncpg](https://github.com/MagicStack/asyncpg), [LICENSE](https://github.com/MagicStack/asyncpg/blob/master/LICENSE) | Apache-2.0 | PostgreSQL driver. Retain license, copyright and any applicable NOTICE; preserve notices and mark modifications if redistributing modified upstream code |

Official upstream licenses and local pinned distribution metadata/license paths were checked. These permissive licenses do not introduce an identified conflict with this GPL-3.0-or-later module; this is a scoped inventory, not certification of every transitive artifact. No driver/engine source is copied into the project. Existing jsonschema/pytest are used for development validation, not new runtime dependencies. Preserve bundled `.dist-info/licenses` and produce a complete SBOM for releases. Discovery engine itself is not a substitute for database administrator authorization.

## Contract validation fixtures

The contract-only test suite retains the official OpenAPI 3.1 meta-schema from [spec.openapis.org](https://spec.openapis.org/oas/3.1/schema/2022-10-07), with upstream [OpenAPI Specification license](https://github.com/OAI/OpenAPI-Specification/blob/main/LICENSE) (Apache-2.0) alongside it at `tests/contracts/fixtures/openapi-LICENSE.txt`. Source URLs, normalization and committed-byte SHA256 values are recorded in `tests/contracts/fixtures/provenance.json`. Preserve that license/provenance when redistributing the fixture; no engine/tool binary or new dependency is introduced. Project-authored examples are synthetic and do not inherit database/runtime provenance.

## Distribution

Before a distributable release, produce a full lockfile/runtime SBOM including transitive packages, actual engine artifact versions, their complete licenses and applicable notices. Re-audit when versions or deployment artifacts change. No license conflict was identified for the direct open-source components used by this integration; this scoped audit does not certify unrelated components.
