# FlowBridge API Contracts 0.2.0

**Contract-first specification for independent implementation.**

Status: **REVIEW CANDIDATE**. Static validation is independent of runtime acceptance. All four databases remain **PENDING_DB_ALIGNMENT**; no ORM, migration, seed or service deployment is implied by these documents. The three-layer IT administration/data grant/business receiver authorization model below is decided; other design/implementation review remains open.

## Sources and ownership

This revision uses the repository architecture, the Core Database v0.2 reference design, the supplied API Contracts v0.1.1 endpoint catalog and its architecture audit. Database v0.2 is a proposed dictionary of 20 tables, not a running schema. No separate `Pasted markdown.md` audit file was found in this checkout; the audit findings supplied with the work are reflected in the decisions and implementation gaps below. No inaccessible external conversation is treated as an architectural decision.

Source precedence: OpenAPI/JSON Schema for wire shape; this document for cross-resource policy, transactions and consistency; synthetic fixtures for examples. Resolve discrepancies in these sources before generating consumer code. Entry points are service boundaries, not database tables.

| File under contracts/ | Responsibility |
|---|---|
| common.openapi.yaml | Shared HTTP components; paths is intentionally empty, not a server |
| core-api.openapi.yaml | Core entry point: identity bindings, department/application/source registry and referenced contexts |
| application-onboarding.yaml | Stateless validation/planning and atomic draft import; existing Manifest 1.0 reused |
| data-contracts.yaml | Single canonical Data Contract definition, fields, constraints and relationships |
| data-governance.yaml | Product/contract lifecycle, discovery, requests/grants, jobs/audit and live read projection |
| operation-registry.yaml | P1 Operation/Tool/permission registry and internal executable descriptor |
| sales-api.openapi.yaml | Proposed P1 Sales business boundary |
| warehouse-api.openapi.yaml | Proposed P1 Warehouse inventory/reservation boundary |
| logistics-api.openapi.yaml | Proposed P1 Logistics shipment boundary |
| platform-api.openapi.yaml | P1 executor and engine-neutral workflow boundary |

Fragments are referenced by the Core entry point, not separately deployed. Shared schemas have one owner; references are relative and resolve offline. Existing `identity-context.schema.json`, Manifest 1.0 and Portal presentation schemas are not redefined. Portal presentation/event schemas are legacy UI assets, not new runtime APIs.

## Scope and compatibility

There are **88 operations: 48 P0 and 40 P1**. All 84 v0.1.1 method/path pairs and operationIds are retained. New operations: onboarding validate, plan, draft import (P0), and explicit retirement of an unused published contract (P1).

| Boundary | P0 | P1 | Readiness |
|---|---:|---:|---|
| Core registry/onboarding/governance | 48 | 14 | Contract-first; adapters/runtime pending |
| Sales | 0 | 8 | PROPOSED / PENDING_DB_ALIGNMENT |
| Warehouse | 0 | 7 | PROPOSED / PENDING_DB_ALIGNMENT |
| Logistics | 0 | 6 | PROPOSED / PENDING_DB_ALIGNMENT |
| Platform execution/workflow | 0 | 5 | P1; no engine runtime assumed |

P0 proves Data Sharing without Agent/Operation/Workflow dependencies. Order Fulfillment is deliberately P1; moving it into a demo milestone also moves its delegation, fencing, transaction and compensation acceptance tests into that milestone. P2 mapping/lineage, advanced row filters, partial fulfillment, multiple warehouses and complete Agent/LLM/chat runtime are not specified as endpoints here.

Revision 0.2.0 is **not wire-compatible in every DTO** with v0.1.1. Canonical contracts, source naming ownership and P0 consumer profile change intentionally; regenerate producer/consumer validators together. Preserving paths/operationIds does not imply payload compatibility. No deployed API or Portal compatibility is claimed.

## Authorization model: three independent layers

| Layer | Authority | Enforcement boundary |
|---|---|---|
| DX-OS administration | IT with PLATFORM_ADMIN manages registry, sources, contracts, onboarding, publication, grant/revoke, Operation/Tool registry, jobs and audit across all departments | Core API/backend, after trusted authentication and current principal-role resolution |
| Shared data reads | An effective IT-issued grant specifies the consumer, product, pinned contract and physical columns | Core checks current grant/bindings; Hasura applies trusted role/session permissions |
| Business operations | Sales/Warehouse/Logistics permissions determine who may create orders, reserve stock, hand over or change business data | Each owning Business API, including receiver-side service delegation/resource checks |

Keycloak authenticates identity; Core resolves the verified (issuer,subject) to an ACTIVE principal and its current `platform_role`. A request role/header, group name or department membership is not that authority. The existing Core role field remains NONE or PLATFORM_ADMIN; Consumer describes a usage category, not a new database role enum. IT may grant/revoke PLATFORM_ADMIN through the authenticated administrative process with audit; bootstrap/service identities require trusted operator setup, not caller assertions.

PLATFORM_ADMIN is full DX-OS control-plane authority, not merely a draft-proposal capability. IT can register/configure/publish and issue/revoke grants without belonging to, obtaining approval from, or impersonating a department OWNER. Technical schema/type/cardinality validation, lifecycle, revision, job and metadata verification still apply. Administrative access to metadata includes disabled/draft resources for diagnosis and management; resource state may still block unsafe commands.

Department staff/managers have **no DX-OS management authority from their business role**. `owner_department_id` is organizational/data affiliation, not an authorization role. STAFF/legacy OWNER membership values are retained only for Core v0.2 compatibility and department-based consumer bindings; neither grants publication, approval, administration or audit access. They must never be mapped automatically to PLATFORM_ADMIN. This model does not change or grant direct PostgreSQL credentials.

Consumers may read their own resolved identity, visible published catalog/contract metadata and their own department-grant status, then read rows covered by an effective grant. They cannot create/approve/cancel access requests, configure drafts, publish, grant/revoke or inspect global administrative jobs/audit. Existing access-request endpoints are retained as **IT-only administrative records** on behalf of a consumer, not a mandatory department self-service approval chain. IT may record and approve a request itself; no separate department approver or four-eyes rule is assumed. No new endpoint or database migration is introduced by this policy change.

When IT uses a normal data/execute endpoint it still follows that endpoint's data/execute policy; no Hasura admin-secret query fallback. IT can deliberately grant the required consumer permission through the audited management APIs, but PLATFORM_ADMIN itself is not a row permission or a Business API write permission. Operation execution and workflow writes also require their distinct execute/delegation and downstream business checks. A service account never becomes an administrator solely because account_type is service.

Each operation declares `x-authorization-policy`: IT_ADMIN, SELF_IDENTITY, CATALOG_READ, GRANT_STATUS, DATA_GRANT, OPERATION_EXECUTE, WORKFLOW_RESOURCE, WORKFLOW_READ or BUSINESS_RECEIVER. These are policy identifiers, not extra user roles. Only IT_ADMIN authorizes Core management; the other identifiers describe bounded consumption/self-service reads or independent runtime/business checks.

## HTTP conventions

- Gateway preserves `/api/v1/core`, `/api/v1/sales`, `/api/v1/warehouse`, `/api/v1/logistics`, `/api/v1/platform`. Each receiver authenticates the correct audience and authorizes the target resource.
- JSON snake_case; UUID resource IDs, opaque IdP/engine IDs, separate business codes. No cross-database foreign keys. UTC RFC3339 timestamps end in `Z`.
- Success DTOs are direct objects; list pages are `{items,next_cursor}`. Cursor is opaque, bound to caller/filter/sort. Every list declares `x-pagination`; default limit 20, maximum 100. Recheck current visibility for every page.
- Requests reject undeclared properties. Public read DTOs redact connection coordinates, users, secret refs, executable query/HTTP configuration and enforcement roles. The internal descriptor/context schemas are **not HTTP responses**.
- X-Correlation-Id is normalized and echoed, including errors. Duplicate/invalid input does not become authority. Error envelope preserves the existing code/message/request_id/retryable boundary. `details` is optional for compatibility; consumers normalize absence to `[]`.
- All sensitive success/error responses declare Cache-Control: no-store. Shared HTTP caches must not replay data or authorization results; durable command replay happens only after current backend policy checks.
- 400 malformed JSON/HTTP; 401 authentication; 403 policy; 404 absent/invisible; 409 state/revision/idempotency/stock conflict; 422 shape/references/profile; 429 with Retry-After; 503 dependency/invalid upstream; 504 timeout; 500 sanitized failure. Never expose raw input credentials or upstream bodies.
- 202 means a durable job/link was accepted, never that publication/enforcement/workflow finished. Poll the resource in Location; Retry-After is seconds. Jobs/audit are not writable public CRUD APIs.

### HTTP idempotency and revisions

Durable POST/PUT/PATCH commands require Idempotency-Key, scoped by verified issuer+subject, method, canonical path and key. Normalize input, fingerprint it, and atomically claim/persist the command result with the owning local transaction. Same key/different payload is IDEMPOTENCY_CONFLICT; an in-flight key returns retryable INVALID_STATE with Retry-After. Validate/authenticate/authorize before claiming a command; rejected input is not a completed mutation.

Completed replay rechecks current visibility and returns the original business result/status, without re-running stale expected_revision checks. Trace IDs reflect the current request; audit correlates to the original command. A new key with an old revision conflicts. Keep HTTP records for at least seven days in the proposed profile, while business uniqueness invariants survive HTTP expiry. This retention and its storage require owner/DB confirmation.

Read-only POST validate/plan does not create a durable command or require a key. Execute-operation is conditional: mutation targets use durable replay and stable downstream keys; read targets only deduplicate in-flight attempts, then replay performs fresh authorization and a fresh read, never cached data rows.

HTTP replay storage is **not** publication_jobs.idempotency_key. The latter identifies target/type/revision work. A general HTTP record store, atomic transaction strategy and retention are persistence gaps, not tables assumed present in v0.2.

PATCH/commands use expected_revision; grants use expected_desired_revision. Membership POST creates an absent pair; restore/reconfigure existing membership with PATCH and CAS. Operation permission revocation has no invented revision column: atomic ACTIVE-to-REVOKED, key and audit.

## Canonical Data Contract

`data-contracts.yaml` owns the canonical document stored in `data_contract_versions.contract_json`. Outer resource `version` is semantic version; document_version identifies the JSON format. This does not add DB columns.

| Concept | Convention |
|---|---|
| column | Physical PostgreSQL column name |
| name | Public field/alias; explicitly equal to column when no alias |
| physical_type | Exact discovered PostgreSQL type, retained independently of wire representation |
| wire_type | Public JSON representation, not PostgreSQL type spelling |
| business_key | Public field names; all must be requested by `/rows` |
| scope_json.columns | Physical names, compatible with provisioner column allowlists |
| relationship mapping | Physical from/to columns; target is application.product key |
| classification/grain/description | IT-declared semantics, optionally informed by the business team; not inferred automatically |

Never pass public aliases directly into a Hasura physical permission allowlist. Resolve both directions through the **pinned** immutable contract. Contract catalog contains the mapping; Portal displays public names but submits physical grant scope. `/rows?columns=` and returned row keys use public names. This distinction is explicit even when examples happen to use identical names.

Fields and relationship public names are unique; no alias collision with physical columns or reserved names. Join mapping is bijective and supports composite keys. Verify column existence, type/nullability/constraints, source kind and full key mapping. Physical constraints are discovered facts; wire constraints are promises to validate, not proof that PostgreSQL enforces them.

Business keys must be non-null and uniquely identify the published grain. Views do not automatically provide uniqueness. Use physical evidence or a fresh IT-reviewed attestation resolved by a trusted policy port, bound to product/columns/schema hash/expiry. IT may consult the business team without delegating DX-OS authority to its membership role. A manifest `uniqueness_ref` is only a reference proposal. Attestation lifecycle/storage needs Core alignment (reviewed contract/audit metadata is a possible minimal design); no new table or implemented evidence provider is asserted. Missing proof blocks publication/planning, not an invented FK.

Same-source object/array relationships use exact FKs or manual mappings. Object targets need uniqueness proof; array targets do not. IT configures cross-source relationships using Hasura remote `to_source` only when target/source/product are verified published/tracked and the configured v2 CE capability is supported. Administrative configuration authority does not require a consumer data grant, and never grants one. Execution/query still checks the consumer's permissions at both ends. Unsupported/unknown stays BLOCKED. No cross-database FK, distributed transaction or implied target-read permission.

P0 `/rows` supports scalar string/integer/number/boolean/date/date-time wire values. Structured object/array values are representable in draft contracts for P1, but P0 publication must block that profile rather than erase fields. Numeric precision is part of publication validation: JSON-safe exact integers within +/- (2^53-1); finite numbers without silent loss. If bigint/numeric cannot meet the promise, require an approved text-producing source view and string contract or reject publication/read. Do not silently coerce jsonb/arrays to text. No SQL is accepted in a manifest or contract.

Date is YYYY-MM-DD; date-time is UTC RFC3339 Z. The entire result is validated against the requested projection, required keys, nullability and constraints; no extra/partial rows or rounding. One invalid row or Hasura errors, including partial data with errors, yields 503 without data.

### Lifecycle

DRAFT content is editable with revision guards. PUBLISHED/RETIRED content and version identity are immutable. A P1 retire command requires the version not be active or referenced by live grants/ACTIVE operations; retirement is not deletion.

A product serves one active contract. IT publishes only a PUBLISHED version belonging to that product after current schema/state validation; contract/product classification must agree. No separate department OWNER approval is required. Switching versions while grants are PENDING_APPLY/ACTIVE/REVOKING/ERROR is rejected; lock against concurrent IT approval. Do not migrate existing grants or operations to latest silently.

## Application Onboarding

The public submission wraps the existing **Manifest 1.0 unchanged**, plus exactly one canonical product_contract proposal per manifest product. This avoids extending a strict parser incompatibly. The annotations supply semantics/wire types absent from introspection; reject conflicting column/alias/description/version or normalized relationships. No dropping metadata to satisfy a smaller HTTP DTO.

1. `validate`: static shape/references/annotation consistency; no network/write. valid is not approval.
2. `plans`: STATIC returns explicit unknown evidence; LIVE uses approved read-only discovery. Return a redacted diff with dependencies, blockers, evidence hashes and current approval requirements. No raw engine payload/IR connection data is returned.
3. IT reviews source policy, resource affiliation, contract and relationships and may approve/publish them directly. No department-owner gate. Caller cannot bypass command policy by submitting approval/status flags. Missing authorization/integration fails closed.
4. `imports`: recompile LIVE facts, reject expired/stale plan_hash, compare application revision (0=create), then save all drafts/audit/HTTP receipt atomically. Active configuration drift is not silently overwritten. A fake adapter cannot acknowledge persistence.
5. Existing source connect, contract publish and product publish commands perform review/approval at their resource boundaries and enqueue ordinary publication jobs. No blanket import-to-grant permission and no new onboarding job_type.
6. Workers provision and verify before CONNECTED/ACTIVE/applied acknowledgements. Access requests/grants remain a separate process.

Plan hashes are preconditions, not authorization capabilities. Bind canonical content, actual discovery and target revisions; recheck at use. STATIC/unknown/error cannot be ready. Plans need not be persisted: a recomputed hash is sufficient. If durable review sessions/attestation history are later required, record their persistence gap explicitly rather than claiming an onboarding table exists.

IT's PLATFORM_ADMIN authority covers draft import, configuration, approval, publication and grant/revoke across departments. Import still only saves drafts because of command semantics, not because IT lacks publish authority; IT invokes publish explicitly. Canonical annotations determine proposed product classification. The current library's mandatory OwnershipResolver/owner_verified and target accessible_products checks are legacy implementation gaps: align them to verified IT administrative authority for configuration, not a required OWNER membership or row grant. Keep physical/cardinality/revision/verification guards and runtime consumer grant checks. This contract revision does not change that runtime code or assert those adapters exist.

Application keys use the existing manifest LogicalKey syntax. Source/product keys are globally qualified as application.local_key, and the prefix must match the actual owning application; they cannot be reassigned. Manifest local keys are qualified before persistence. For direct SourceCreate, resolve the application and strip only its exact prefix before invoking the same deterministic engine-name algorithm used by manifest onboarding. Hasura source names are server generated, never request controlled. Secret references are opaque operator-approved inputs; source bindings must match host/port/database/user/TLS exactly. DSNs/passwords never enter these contracts.

Discovery returns PK/unique/FK/composite metadata needed by the compiler, not only columns. The source-only schema endpoint requires a configured approved bounded scope; optional requested filters may only narrow it. No unrestricted default or track-all behavior. No response truncation disguised as a complete snapshot. Timeout/size limits and missing integration fail explicitly; schema_snapshots persistence is not required to claim a read-only schema response.

## Identity, grants and Hasura

VerifiedIdentity is reused from the existing JSON Schema. Resolve (issuer,subject) to trusted principal/platform_role and active membership/application bindings. Humans have no application/service-client binding; service account client binding is verified from IdP/config. PLATFORM_ADMIN authorizes DX-OS management. A valid token, group name, registration or Tool binding alone does not grant management, data reads, operation execution or business writes; business/data rights remain independent of PLATFORM_ADMIN.

**P0 grants are department-only.** The DB reference supports principal XOR department, but that broader storage capability is not a runtime promise. Non-null consumer_principal_id is rejected by the P0 API, not widened to a department. Principal read grants are P1 and require a reviewed enforcement/role/revoke design before exposure. P1 operation execute permissions remain separate from read grants and downstream business authorization.

Product -> Source -> Application -> Department determines metadata affiliation and grant grouping only. IT creates administrative requests on behalf of consumer departments and approves/rejects/cancels them; consumers have no management workflow here. Catalog metadata is organization-visible for ACTIVE products/current PUBLISHED contracts while affiliated entities/source remain active; drafts/history/global jobs/audit are IT-only. CONFIDENTIAL labels protect rows through grants, not metadata secrecy. Catalog confidentiality requires a later explicit ACL decision.

For each read select one current grant covering the entire physical projection including mapped business keys. Check ACTIVE principal/membership/department/application/product, CONNECTED source, current PUBLISHED contract, grant expiry and applied_revision=desired_revision. Do not union unrelated subject grants. Department disable/revoke/expiry denies new requests even before reconciliation. In-flight reads may finish within timeout; no promise of instantaneous cancellation.

Feasible v2 CE profile: Core-generated bounded read queries, Hasura authentication using a trusted JWT/webhook configuration, and server-derived department roles. An internal Core policy port returns TrustedHasuraContext; it is not client input. Prefer a current-policy webhook profile when immediate request-time revocation is required; JWT claims alone must not be treated as fresh membership/grant state. Hasura data endpoint must not expose a route that bypasses equivalent current policy; restrict direct ingress and keep the Metadata API private. The exact hook/token transport is an implementation gate, verified against the deployed CE build before use, not a custom authorization engine or Enterprise feature.

Control-plane admin secret is only for provisioning; **never normal data queries**. Backend/Hasura must receive matching operator-managed from_env bindings. Python does not automatically provision a secret into Hasura. No long-lived role token, broad service credential or caller role header may bypass resource policy.

## Governance, durable jobs and audit

Source CONNECTED, product ACTIVE, grant ACTIVE are different verified states. READY/PROVISIONING are internal product transitions. Approving a request atomically writes decision, PENDING_APPLY grant and APPLY_GRANT job, after locking/version-checking request AND product/current contract and checking effective bindings/scope subset. Unique request/grant/subject invariants survive retries and different HTTP keys.

Worker claims use atomic lease/attempt fencing and DB time; renew/finish checks owner, attempt and unexpired lease. External Hasura calls are outside the Core DML transaction. Revalidate target_revision and current approval before mutation and CAS again after read-back/consistency verification. A metadata version conflict is not permission to retry blindly. Timeout may mean the engine committed: inspect actual state then reconcile, not start from a baseline overwrite.

Revoke increments desired_revision and writes REVOKING/job atomically; read policy denies immediately. Applied acknowledgements require the current desired revision. Stale jobs fail with STALE_REVISION; they cannot resurrect rights. Expiry is checked every request then reconciled. Disabling a product denies reads first and reconciles all affected permissions through DISABLE_PRODUCT; do not merely change a status label. Preserve other subjects' valid permissions.

Audit append-only events retain verified actor/initiator, correlation, target/hashes and sanitized outcome; no raw bearer, secret ref/coordinates, query result or unsanitized diff. Enqueue/desired-state/audit are committed together where required. Public audit output is a selected summary, not raw diff_json. Missing production adapters must return unavailable, never mock success.

## P1 execution and workflow boundaries

Agent -> Tool -> pinned Operation -> current execute permission -> Hasura grant or Business receiver policy. Registry owns canonical input/output schemas; Tool does not copy them. ACTIVE versions are immutable. BUSINESS_API request_config_json pins exact Domain operationId, method and path; variables have path/query/body and canonical target validation. No guessed mapping, eval, arbitrary URL/query/role/actor/secret input. Follow no redirects; validate approved base URL, DNS/egress and target application/department at activation and execution. Source/HTTP host allowlists do not replace deployment network policy.

Core public Operation reads deliberately omit query/config/secrets. Platform defines an internal resolver port by operation key+version; a composition adapter supplies ResolvedOperationDescriptor plus current policy. Platform does not import Core repositories, and no public endpoint exposes the internal descriptor. This interface/persistence is not claimed to exist already.

CALLER_TOKEN has null secret_ref and receiver audience validation. SERVICE_CREDENTIAL requires an operator reference, caller execute permission and trusted resource/run delegation; it never elevates a human caller implicitly. Downstream mutation keys include verified caller and operation version even when callers share one executor credential. Only synchronous 200/201 JSON Domain targets are supported in this profile. Preserve sanitized caller/business errors; receiver credential failure or invalid upstream/schema ->503; timeout ->504 with outcome unknown. Validate the whole result; GraphQL errors/partial data are not success.

WorkflowStart accepts application/workflow/business/run identifiers only. Backend chooses engine/configured execution identity; public APIs remain independent of Flowable or another adapter. Core stores business links, not engine definitions/tasks/history. Persist durable start intent and PENDING_START before external work. Correlate timeout/retry by the same tuple; lost 202 is recovered by exact lookup. 404 or timeout is not proof a start cannot still commit. Rerun_number is policy-gated and requires fencing/reconciliation of the previous run, not merely a new key.

Domain profile is PROPOSED: one logical warehouse, full-order reservation, one shipment, no partial/backorder/return/payment/PII model. SKU duplicates invalid; reserve verifies Sales SUBMITTED exact revision/lines and atomically locks/conditionally updates all SKUs. Unique order reservation and reservation shipment outlive HTTP keys. Reserve/read alone is not evidence of ownership or stock availability later.

Confirm needs RESERVED evidence; pack does not consume; shipment create and handover need Sales CONFIRMED. Handover and release arbitrate locally and exclusively. Pickup verifies CONSUMED/matching shipment; fulfill verifies DELIVERED. No terminal order while accepted forward commands have unknown outcomes.

Compensation persists COMPENSATING, fences new forward commands and drains/reconciles already accepted reserve/create requests, then releases, cancels CREATED shipment and cancels/rejects order as policy permits. Preflight run checks do not stop a late commit. After CONSUMED no release; returns are a different process. DRAFT staff cancellation is separate from scoped service compensation after submit. Delegation/fencing mechanism and durable runtime intent require Platform/Business design approval before P1 runtime acceptance; no JSON actor or process-local BackgroundTasks substitutes for it.

## Database alignment and implementation gaps

| Reference ownership | Contract mapping / remaining work |
|---|---|
| principals, departments, memberships, applications | Trusted identity/platform_role; memberships group consumers, STAFF/OWNER are not Core management permissions |
| data_sources | PostgreSQL tuple/opaque ref on writes; generated engine name; redacted read projection |
| schema_snapshots | Optional persistence of typed bounded discovery; no new snapshot table assumed |
| data_products | One physical object/product; derived department affiliation is not authority; active contract and lifecycle unchanged |
| data_contract_versions | Canonical JSON lives in contract_json, outer version/lifecycle unchanged; aliases/relationships do not require new tables |
| access_requests/access_grants | Existing decision/subject/scope/revision fields; P0 exposes only department subject; atomic policy/constraints need implementation |
| publication_jobs/audit_events | Existing job types, revisions, lease/retry and audit metadata; public DTOs redact internal fields |
| operation/tool/agent bindings | P1 metadata; Agent CRUD/model runtime is outside this profile, binding requires a separately valid Agent |
| workflow_business_links | Existing engine-neutral correlation fields; durable start/compensation/fencing state is an explicit adapter persistence gap |
| HTTP idempotency/cardinality evidence | Storage/retention/review mechanism PENDING_DB_ALIGNMENT; do not equate HTTP records with job keys or invent completed migrations |
| sales_db/warehouse_db/logistics_db | DTOs proposed from business semantics; no claim of tables/columns/seed data; DB owners confirm local constraints and transactions |

Existing code gaps to implement later, without changing it in this revision: canonical annotations/mapping, lifecycle-aware compiler context (draft versus published), replacing mandatory owner/dept approval in onboarding/configuration with the IT administration policy, real Core repositories/platform_role resolution, HTTP entrypoints and error mapping, durable idempotency/jobs/audit, runtime Data Contract validator and non-admin rows adapter, relationship/custom-name dispatch/verification, current-policy Hasura auth, internal operation resolver/executor and workflow/domain runtimes. Existing P0 services are reusable foundations, not complete HTTP/runtime implementations.

## Static validation

Run from repository root, with existing locked dependencies:

```sh
uv run --locked pytest tests/contracts -q
uv run --locked ruff check tests/contracts
uv run --locked ruff format --check tests/contracts
```

Tests parse YAML with duplicate-key rejection, resolve all local refs, bundle each entry point in memory and validate against a pinned official OpenAPI 3.1 meta-schema, check component Draft 2020-12 schemas, unique operationIds/service boundaries, request/response/header/security consistency, declared redaction, priorities, legacy paths/IDs and synthetic positive/negative fixtures. Test utilities bundle only for verification; there is no new runtime generator, dependency, network request or database connection.

Static verification on 2026-10-23: **384 passed**; Ruff lint and format check passed. Coverage includes 10 YAML files, 122 named component schemas, six independently bundled/validated OpenAPI documents (five service entry points plus the shared component library), and 54 synthetic cases (27 shape-valid, 27 intentionally invalid). A policy matrix checks all 88 operations: IT administration, bounded consumer access and independent business/runtime permissions. Metadata OWNER without PLATFORM_ADMIN is not management authority; an IT principal may have no department membership. These are specification assertions, not executed authorization decisions. Status is **STATIC VALIDATED / REVIEW CANDIDATE**; runtime acceptance remains DEFERRED. This supersedes the unavailable 79-test evidence cited by the external v0.1.1 manual only for this checkout's specification validation, not for engine behavior.

The OpenAPI meta-schema does not prove business invariants or fully validate every embedded schema by itself; component JSON Schema checks and targeted assertions supplement it. Safe schema subsets, duplicate semantic names, dynamic row projections, cross-record conditions and real authorization still require implementation. Synthetic fixture validity means shape validity, not permission/transaction success.

Upstream schema/license provenance and committed-byte checksums are in tests/contracts/fixtures/provenance.json. Apache-2.0 license is retained beside the fixture; no OpenAPI tool/engine binary is vendored.

## Deferred runtime validation

All cases below are **DEFERRED**, not failed contract validation.

| Prerequisite | Module | Behavior to verify | Test type |
|---|---|---|---|
| Core DB models/adapters and IdP test realm | Core/Identity | IT management without department membership; consumer/legacy OWNER denied management; disabled bindings and redacted metadata (AC-01/12/13) | Auth/API integration |
| Core DB transactions/CAS/job worker | Governance | Double approval, immutable publication, switch/disable races, HTTP replay (AC-02/03/11/18) | Concurrent DB/API integration |
| Disposable PostgreSQL + configured Hasura v2 CE | Data access | Correct columns/aliases/types, latest committed source values, no partial/rounded data, no admin secret (AC-06/14) | Live data-path integration |
| Current-policy auth and worker | Governance/Hasura | Immediate new-request denial for revoke/expiry, preserve independent rights (AC-04) | Security integration |
| Durable jobs and two workers | Provisioner | Timeout after external commit, stale revision, expired/reclaimed lease (AC-05) | Crash/concurrency integration |
| Business databases and approved delegation/fencing | Sales/Warehouse/Logistics | Concurrent stock, evidence, release-vs-handover, late shipment and compensation (AC-07/08/15/16) | Business transaction/distributed integration |
| P1 registry/executor/workflow adapter | Platform | Permissions not interchangeable, output schema, lost start 202, unknown outcome, caller-isolated keys (AC-09/10/17) | Adapter/engine integration |

Before overall baseline approval, implementing teams confirm canonical format/mapping, department-only P0, seven-day HTTP retention/storage, catalog visibility, evidence policy, Business assumptions and P1 delegation/fencing. The IT administration/data grant/business receiver separation is already the chosen authorization model. STATIC VALIDATED and REVIEW CANDIDATE can coexist; neither means the four databases or runtime are ready.

## Official references

- OpenAPI 3.1.1: https://spec.openapis.org/oas/v3.1.1.html
- Official 3.1 meta-schema: https://spec.openapis.org/oas/3.1/schema/2022-10-07
- Hasura v2 CE authentication: https://hasura.io/docs/2.0/auth/authentication/index/
- Hasura webhook boundary: https://hasura.io/docs/2.0/auth/authentication/webhook/
- Hasura relationships: https://hasura.io/docs/2.0/api-reference/metadata-api/relationship/
- Hasura remote relationships: https://hasura.io/docs/2.0/api-reference/metadata-api/remote-relationships/
