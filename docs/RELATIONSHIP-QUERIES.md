# Governed relationship queries

## Scope and evidence

Core now exposes an additive relationship registry and a structured nested read endpoint. Existing `GET /data-products/{id}/rows`, Manifest v1 and department SELECT grants remain unchanged. No business service, frontend, Docker service or business schema is modified.

The deployment profile pins Hasura GraphQL Engine **v2.48.16 CE**, including an image digest. A read-only local `/v1/version` check returned `v2.48.16` during implementation. This verifies the running version, **not** successful relationship provisioning or a real multi-source query.

Offline tests exercise HTTP handlers, Core policy, compilation, metadata request/readback, retries and fencing with explicit test doubles. PostgreSQL integration and real Keycloak/Hasura acceptance are opt-in; neither is represented by mocked success. The new real E2E is **BLOCKED until approved migration and fixture configuration are supplied**. Existing single-product E2E evidence does not prove this new feature.

Validation performed: `pytest tests/core tests/platform tests/contracts tests/domain -q` completed with **783 passed, 13 deselected**; Ruff check and formatting checks passed. Explicit collection/execution of the new PostgreSQL integration and real E2E harnesses without live opt-in configuration yielded **2 skipped**, not PASS. Windows asyncio tests required execution outside the filesystem/network sandbox to create their local socketpair; the offline tests still used mocked upstreams and no real database. No Supabase migration, fixture write, metadata mutation, commit or push was performed.

## Registry and lifecycle

All paths have prefix `/api/v1/core` and require a verified Keycloak Bearer token.

| API | Authority / behavior |
|---|---|
| `POST /relationships` | IT creates a validated DRAFT |
| `GET /relationships?source_product_id=...` | IT lists outgoing configurations |
| `GET /relationships/{id}` | IT inspects revision, state and error code |
| `PATCH /relationships/{id}` | IT updates mapping in DRAFT or fully DISABLED state |
| `POST /relationships/{id}/publish` | IT queues provisioning with expected revision |
| `POST /relationships/{id}/disable` | Immediately blocks Core traversal; worker removes metadata |
| `POST /data-products/{id}/query` | Consumer reads a bounded selection tree under active grants |

Management commands require `Idempotency-Key`. Publish takes `expected_revision`; disable also takes `reason`. Both return 202 and a normal publication job. Poll the existing publication-job endpoint. A 202 is never evidence that a relationship is ACTIVE.

```json
{
  "name": "product",
  "source_product_id": "11111111-1111-4111-8111-111111111111",
  "target_product_id": "22222222-2222-4222-8222-222222222222",
  "relationship_type": "object",
  "field_mapping": {"sku": "sku"}
}
```

Mappings use **physical** column names, including composite keys. Query fields use **public contract** names. Source/target database, schema and table are resolved from existing Product → Source records, not duplicated or accepted from the query caller. Registry rows pin both published contract versions. Updating a Product's active contract invalidates traversal until the relationship is disabled, revalidated and republished.

`object` requires an actual PK/UNIQUE proof covering the target mapping; `array` requires a PK/UNIQUE proof covering the source mapping. An arbitrary uniqueness assertion, duplicate values in an unconstrained column, incompatible types, unpublished endpoints and conflicting names are rejected. Existing discovery exposes foreign keys as suggestions only; it never creates or publishes a relationship automatically. Cycles and self-references can be registered; query limits bound traversal.

Lifecycle: DRAFT → PROVISIONING → ACTIVE; disable → DISABLING → DISABLED. Exhausted publish/removal failures become ERROR. Retry uses the existing lease, heartbeat, attempt limit, backoff and restart recovery. ACTIVE is written only after Hasura readback and consistency checks. Mapping edits require confirmed removal first; changing name, target or cardinality uses a new registry entry. No active in-place metadata replacement, cascade delete or reverse-relationship generation is performed.

Same-source relations reuse the existing `relationship_operation` compiler and manual column mapping with `pg_create_object_relationship` / `pg_create_array_relationship`. Cross-source relations use `pg_create_remote_relationship` with `definition.to_source`. Removal checks the expected definition and uses non-cascading delete. Writes carry metadata `resource_version`; conflicts/drift fail closed rather than replacing unrelated metadata. Publication and removal serialize per source Product through the existing PostgreSQL advisory-lock boundary. Core job revision/lease fencing is checked before and after external calls.

Canonical relationships embedded in Manifest v1 contracts remain planning declarations under their existing compatibility restrictions. This implementation does not silently convert them into published registry entries. Register relations explicitly **after** their endpoint products become ACTIVE; onboarding still saves drafts without automatic publication or grants.

`contracts/fixtures/relationship-onboarding.json` contains three illustrative Manifest v1 submissions, separate registry requests and a nested query. Hostnames, secret references, fixture schemas and UUIDs are placeholders, not deployment instructions or live data.

## Structured query

```json
{
  "select": ["id"],
  "where": {"id": {"_eq": "ORD-001"}},
  "limit": 1,
  "include": {
    "lines": {
      "select": ["id", "sku", "quantity"],
      "limit": 5,
      "include": {
        "product": {
          "select": ["sku", "name"],
          "include": {"supplier": {"select": ["name"]}}
        }
      }
    }
  }
}
```

An optional `contract_version_id` pins each node; otherwise Core selects its current active contract. Response contains root product/contract IDs, `rows`, `limit` and `offset`. Hasura performs all local/remote joins in **one GraphQL request**; Core validates the nested JSON without joining or flattening business rows. Missing object targets/null join keys are represented by Hasura as `null`; empty arrays are `[]`. GraphQL partial errors, malformed responses, excessive output and unavailable dependencies never return partial business data.

Root and array nodes support scalar-only `where`, scalar `order_by`, `limit` and `offset`. Operators: `_eq`, `_neq`, `_gt`, `_gte`, `_lt`, `_lte`, `_in`, `_is_null`; booleans only permit equality, membership and null checks. Inputs become typed GraphQL variables. Ordering appends the contract business key as a stable tie-breaker. Object nodes accept selection/include/contract pin, **not** filtering or pagination arguments. Remote array arguments are emitted as normal Hasura v2 target-table selection arguments; their real engine acceptance remains to be verified by the opt-in E2E environment.

There is no raw GraphQL/SQL endpoint, relationship predicate in a parent's `where`, sorting a parent by a remote field, arbitrary GROUP BY/HAVING, aggregation, distinct, subscriptions or flattened cross-database analytics. Unsupported operations return validation errors instead of being evaluated in Python. Existing physical/wire scalar restrictions remain; this change does not add NUMERIC/BIGINT/JSON or unconstrained VIEW publication support.

## Authorization and safety

- Every selected node requires one matching ACTIVE, applied, unexpired SELECT grant. Publication alone grants nothing; PLATFORM_ADMIN has no implicit row access.
- **One common consumer department must cover the complete graph.** Combining Sales grants for one node and Warehouse grants for another cannot safely use the existing single Hasura role and is denied. No new tenant model is introduced into the single-enterprise Core.
- Grants must cover selected columns, filter/order columns, stable business keys and both ends of every join mapping, even when those keys are not returned.
- The current profile only supports `row_scope: ALL`. Any other stored row restriction is denied, never silently discarded. Hasura's exact verified permissions must match the existing grant before dispatch.
- Core verifies current principal/membership/application/source/product/contract/grant/revision state and live schema, source configuration, mapping, relationship metadata and permissions. Discovery snapshots are reused **within one query only**, not cached across business reads.
- A backend-issued short-lived non-admin Hasura JWT uses the selected department role. Caller-supplied role/source/session headers are not forwarded. Metadata admin credentials are never used for row execution.
- A second Core authorization/state check after the Hasura response rejects revocations, disabled relationships and changed bindings that occurred during the read. It cannot retroactively cancel an already executing database query or revoke a response already delivered.
- Metadata consistency/resource version is checked again after execution; concurrent metadata changes discard the response. Remote reads do not provide a distributed transaction or an atomic snapshot across independent business databases.
- Query audit persists actor, root Product, relationship IDs, decision, duration and request ID, without token/filter values/business rows. Audit write failure prevents a successful response.

Defaults are configurable with `FLOWBRIDGE_QUERY_`: `MAX_DEPTH=4` relationship edges, `MAX_NODES=20`, `MAX_ROWS=100`, `MAX_ARRAY_ROWS=20`, `MAX_COMPLEXITY=10000`, `MAX_REQUEST_BYTES=65536`, `TIMEOUT_SECONDS=20`. Complexity bounds projected fields multiplied by worst-case array fan-out. Existing ASGI body limit, streaming response cap (8 MiB) and Hasura timeout remain active. Keep Hasura GraphQL ingress private; only Core should distribute data to consumers. Database owners should index join keys; no business index or schema is created automatically.

## Migration and running

`core_0005` adds one registry table, a nullable job target FK and new job kinds/index/constraints. It does not reset Core or alter business data. It has **not been applied to Supabase by this task**. Review and approve the database change before deployment. Downgrade refuses to discard registry/job history; existing `core_0004` historical reset guards remain unchanged.

After an approved migration through the existing database tooling, start the same native processes; Docker scope remains Keycloak + Hasura only:

```powershell
.venv/Scripts/python.exe -m uvicorn app.core.runtime:create_app --factory --host 127.0.0.1 --port 18000
.venv/Scripts/python.exe -m app.core.runtime worker
.venv/Scripts/python.exe -m pytest tests/core/runtime/test_relationships.py tests/core/db tests/contracts -q
```

The worker CLI accepts the existing runtime configuration. No migration, realm change, source bootstrap or container restart occurs automatically.

## Real acceptance prerequisites

`tests/core/persistence/test_relationships.py` uses the existing isolated PostgreSQL fixture, **only** when `DXOS_TEST_ADMIN_URL` is explicitly set; it never falls back to application URLs. It tests the migration, durable registry/receipt, process-composition restart and job claim. It does not prove Hasura behavior.

`tests/e2e/test_relationship_queries.py` calls real Core HTTP authentication and a separately running Worker/Hasura, without mocks or identity overrides. It does not create a database or run migrations. An operator must first approve/provision:

1. Core at `core_0005`, real Keycloak principals/memberships and a consumer department; fresh IT/consumer tokens in environment variables.
2. Three distinct existing PostgreSQL databases, and four fixture tables in reserved `fb_rel_e2e_*` schemas: Sales `orders(id text PK)` and `order_lines(id text PK, order_id text, sku text, quantity integer)`; Warehouse `products(sku text PK, name text, supplier_id text)`; Procurement `suppliers(id text PK, name text)`. Add the agreed constraints/nullability and deterministic rows.
3. Operator-approved source scopes/readers and Hasura `from_env` bindings for these tables. A fixture-only Warehouse writer is supplied for the source-update assertion. Never reuse production tables for this harness.
4. Three unique Manifest v1 submissions with canonical product contracts and application keys beginning `rel-e2e-`; do not embed relationship declarations. Keep files, tokens and writer credentials outside Git.

Set `FLOWBRIDGE_RELATIONSHIP_E2E_CONFIG` to a local JSON file with `allow_fixture_mutations: true`, `core_url`, `hasura_url`, `consumer_department_id`, `it_token_env`, `consumer_token_env`, `warehouse_write_url_env`, `order_id`, `product_sku`, `expected_rows`, and `submissions` mapping `sales`, `warehouse`, `procurement` to local submission filenames. `expected_rows` follows the selection tree above. The writer URL must match the Warehouse manifest target.

```powershell
.venv/Scripts/python.exe -m pytest tests/core/persistence/test_relationships.py -m integration -q
.venv/Scripts/python.exe -m pytest tests/e2e/test_relationship_queries.py -m e2e -q --junitxml=.tmp/relationship-e2e.xml
```

The E2E onboards/publishes four products, creates local and two remote relationships, proves denial before target grants, reads the nested response, checks source freshness, measures legacy/single/nested latency and verifies Warehouse revoke while Sales-only queries remain readable. It restores the fixture name and revokes/disables created resources in `finally`, without deleting historical metadata or resetting databases. Inspect cleanup failures before rerunning. Configuration absence reports SKIP/BLOCKED, never PASS. Composite mapping, cyclic graphs, partial failures, schema/metadata drift, missing columns and lease/retry edge cases have offline coverage; they are not all claimed as real E2E coverage.

## Official references

- [Postgres remote-source relationships](https://hasura.io/docs/2.0/schema/postgres/remote-relationships/remote-source-relationships/)
- [Local relationship Metadata API](https://hasura.io/docs/2.0/api-reference/metadata-api/relationship/)
- [Remote relationship Metadata API](https://hasura.io/docs/2.0/api-reference/metadata-api/remote-relationships/)
- [Permissions](https://hasura.io/docs/2.0/auth/authorization/permissions/)
- [Security best practices](https://hasura.io/docs/2.0/security/security-best-practices/)
