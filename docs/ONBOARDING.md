# Declarative Application Onboarding

**Application Manifest v1 -> PostgreSQL schema discovery -> validation -> Core drafts/contracts -> Hasura operation preview.**

IT khai báo ứng dụng và dữ liệu muốn chia sẻ, không khai báo lại database schema hoặc viết Hasura Metadata JSON. Nền tảng chỉ đọc cấu trúc nguồn và sinh kế hoạch; không tạo table/view/FK, không copy dữ liệu nghiệp vụ, không tự publish hay cấp quyền.

## Phạm vi hiện tại

| Có implementation | Chưa có integration thật |
|---|---|
| Pydantic v2 manifest, JSON Schema, reference/duplicate validation | Core SQLAlchemy adapters và persistence |
| SQLAlchemy Inspector qua asyncpg, transaction read-only, timeout và secret/host policy | Kiểm chứng PostgreSQL/Hasura thật |
| Typed discovery DTO, normalized IR và FK suggestions | Trusted owner/authorization implementation |
| Core registration/contracts + Hasura source/table/relationship plan | Durable publication dispatcher/worker và audit transaction |
| CLI validate/discover/plan và schema export | CLI apply/status intentionally disabled |

Không có public HTTP API mới; API team tiếp tục sở hữu OpenAPI. Service/DTO là integration boundary, không phải ORM hoặc một deployment/service mới.

## Source và bố cục

```text
backend/app/core/
  applications/
    manifest.py       Application Manifest v1, JSON Schema, stable names và hashes
    planning.py       Validation, normalized IR, Core/Hasura plan và dependency gates
    onboarding.py     Ownership -> discovery -> atomic draft port -> approved job port
    cli.py            argparse CLI, không có production fake adapter switch
  data_sources/
    discovery.py      SQLAlchemy Inspector adapter và typed physical schema
  hasura_provisioner/
    planning.py       Pure Hasura v2 payload builders, không gọi HTTP
    client.py         Metadata Client hiện hữu, reuse cùng source payload builder

contracts/onboarding/
  application-manifest-v1.schema.json
  examples/           Warehouse, Sales cross-source và offline discovery fixture
tests/core/onboarding/ Unit/contract tests với metadata tổng hợp
```

## Định dạng manifest

Mẫu chuẩn: [Warehouse](../contracts/onboarding/examples/warehouse.json). Hai view `public.product_catalog` và `public.stock_available` phải tồn tại sẵn. [Sales cross-source](../contracts/onboarding/examples/sales-cross-source.json) tham chiếu `warehouse.product_catalog` từ Core registry, không đăng ký lại database của Warehouse dưới quyền Sales.

- `manifest_version`: hiện chỉ `1.0`; version khác bị từ chối. Khi nâng format, thêm schema/parser version mới và migration khai báo có review; không âm thầm diễn giải version lạ bằng rules v1. JSON Schema được export từ model; test ngăn file schema lệch model.
- `application`: stable key/name, department reference và description. Department key là đề xuất tham chiếu, không phải bằng chứng sở hữu. Trusted resolver kiểm cả ứng dụng hiện hữu và phòng ban, không cho caller tự chuyển ownership.
- `sources`: logical key, PostgreSQL connection tuple, SSL policy và opaque `secret_ref`. Không có password/DSN trong manifest. Default TLS `verify-full`; `require` chỉ mã hóa, không xác minh danh tính server, cần policy review riêng.
- `data_products`: một object vật lý cho mỗi product, fields công bố, contract version và `DRAFT` hoặc `PUBLISH_REQUESTED`. Requested state không phải approval. Manifest không có role/owner/grants/admin command fields.
- `fields`: physical column, optional alias/type expectation/description. Type/nullable thực lấy từ discovery. Alias đổi tên GraphQL, không cấp quyền và không tự ẩn các cột không khai báo; quyền SELECT vẫn phải dùng physical column allowlist từ Core grants.
- `relationships`: source là product chứa declaration; target local dùng `product_key`, target ngoài ứng dụng dùng `application.product_key`. Source/physical target được resolve từ product, không lặp host hoặc engine source trong JSON. Composite mapping dùng danh sách `from`/`to`, không trùng đầu nào.

Identifiers vật lý/public v1 giới hạn lowercase ASCII snake_case; logical keys còn cho phép dấu gạch nối. Reserved GraphQL names `__...`, unknown fields, duplicate JSON object keys, duplicate products/sources/fields và reference nội bộ không tồn tại đều bị từ chối. JSON Schema kiểm shape; Pydantic còn kiểm semantic references/uniqueness mà JSON Schema không biểu diễn đầy đủ.

## CLI

Từ root repository, sau `uv sync --locked --group dev --group build`:

```sh
uv run --locked flowbridge onboard validate --config contracts/onboarding/examples/warehouse.json --json
uv run --locked flowbridge onboard plan --config contracts/onboarding/examples/warehouse.json --snapshot contracts/onboarding/examples/warehouse-discovery.json --json
uv run --locked flowbridge onboard schema --json
```

Nếu chưa cài console entry point vào virtualenv, đặt `PYTHONPATH=backend` rồi dùng `python -m app.core.applications.cli` thay `flowbridge`. Output hiện là structured JSON; `--json` được hỗ trợ tường minh cho integration scripts. Exit 2 cho lỗi input/physical validation hoặc lệnh bị khóa.

`validate` không kết nối DB. `plan` mặc định không kết nối network và không side effects; thiếu discovery/Core/Hasura/secret bindings sẽ báo UNKNOWN/BLOCKED thay vì giả có source/approval. `--snapshot` là offline evidence, **không chứng minh** database đang có cấu trúc đó. `valid=true` chỉ có nghĩa không phát hiện lỗi chắc chắn; xem `ready_for_dispatch` và blockers, không coi đó là đã publish.

Đọc schema từ PostgreSQL thực chỉ khi operator cấp allowlist và secret bindings ở môi trường cục bộ, rồi chủ động chạy:

```sh
uv run --locked flowbridge onboard discover --config contracts/onboarding/examples/warehouse.json --json
```

Discovery reuse `FLOWBRIDGE_HASURA_ALLOWED_DATABASE_HOSTS` và `FLOWBRIDGE_HASURA_SECRET_BINDINGS` từ `.env.example`; không cần Hasura admin secret. Opaque ref như `warehouse-reader` map sang một environment name do operator quản lý; DSN thật đặt trong secret runtime và phải khớp host/port/database/user/SSL của declaration. Không dùng config/credential Supabase production để thử ví dụ. Ví dụ dùng `.example.invalid` nên không phải địa chỉ triển khai.

Inspector liệt kê schemas và table/view names trong schemas được yêu cầu, chỉ reflect objects được manifest chọn: columns/types/nullable, PK, unique constraints và FK/composite FK. Chỉ chạy hai session commands cố định `SET TRANSACTION READ ONLY` và statement timeout; không nhận SQL từ JSON. Không SELECT dữ liệu nghiệp vụ, không scan uniqueness bằng dữ liệu. Snapshot không phải data-quality certificate. Engine được dispose sau từng discovery, kể cả khi lỗi; NullPool, timeout và sanitized errors được áp dụng. OS CA trust được dùng cho TLS; custom CA/IPv6/quoted identifiers là phần cần mở rộng có kiểm thử.

`apply` và `status` trả Missing Integration hiện tại. CLI không có cờ fake adapter hay bypass authorization. Library orchestration có ports để Core team ghép thật, nhưng không có production adapters/entrypoint được wiring trong task này.

## Relationship semantics

| Declaration | Điều kiện | Hasura v2 preview |
|---|---|---|
| Same-source `fk` object | FK vật lý trên source khớp đầy đủ mapping | `pg_create_object_relationship`, local constrained columns |
| Same-source `fk` array | FK trên target trỏ về source, mapping reverse khớp | `pg_create_array_relationship`, qualified target table + constrained columns |
| Same-source `manual` object/array | Columns/types hợp lệ; object còn cần unique proof | `manual_configuration` cho table/view |
| Cross-source `remote` object/array | Core-resolved published target, tracked metadata, discovered columns/types; engine profile xác minh | `pg_create_remote_relationship.definition.to_source` |

Object relationship cần target PK/global unique constraint, matching physical FK, hoặc cardinality evidence từ trusted Core/owner resolver. `uniqueness_ref` trong manifest **không phải** sự xác nhận. Views thường không có FK/unique constraints, nên ví dụ Warehouse mặc định báo `CARDINALITY_UNPROVEN` tới khi có bằng chứng được kiểm chứng. Proof phải khớp product/column set/schema snapshot hash và chưa hết hạn. Owner attestation là assumption có quản trị, không được mô tả thành ràng buộc PostgreSQL đã tự thực thi.

Array không đòi target unique. Partial unique indexes không được coi là bảo đảm unique toàn tập. Không suy FK từ tên cột giống nhau. Join keys phải nằm trong fields của hai contracts ở MVP. Type comparison bảo thủ theo canonical PostgreSQL type, không tự thêm cast hay quan hệ ngữ nghĩa.

Remote source relationships được đối chiếu với Hasura v2 CE profile `v2.48.16` và official API; compiler chỉ bật branch này khi trusted context xác nhận profile đó, không lấy version/capabilities từ manifest. Profile unknown -> BLOCKED. Cross-source không phải FK vật lý hoặc transaction xuyên DB. Target ngoài app chưa resolve/publish/track -> BLOCKED. Trusted Core adapter phải xác minh quyền tương ứng của actor và cung cấp `accessible_products`; thiếu bằng chứng này -> `TARGET_ACCESS_REQUIRED`, kể cả khi target đã published. Compiler không tự thêm SELECT permissions; runtime caller vẫn cần quyền phù hợp ở cả source/target theo Core/Hasura authorization.

## Plan và idempotency

`JSON -> validated declaration -> physical facts -> normalized IR -> Core drafts/contracts + Hasura payloads`.

Plan phân biệt `CREATE`, `UPDATE`, `NO_CHANGE`, `BLOCKED`; verification là KNOWN hoặc UNKNOWN. Steps có dependencies: Core registry/contract -> Hasura source -> selected tables/views -> relationships. Permission phase luôn ghi policy `CORE_GRANTS_REQUIRED_NO_PERMISSIONS_GENERATED`. Blocker lan tới dependent steps; không có bước tạo quyền ngầm từ fields.

Core definitions được so canonical hash với state do adapter cung cấp. Source logical key được namespaced thành `application.source_key`, product thành `application.product_key`, tránh đụng UNIQUE giữa ứng dụng. Hasura source name được sinh ổn định từ application/source và hash; không nhận raw engine name từ manifest.

Contract đã có cùng version nhưng nội dung đổi -> `IMMUTABLE_CONTRACT`, cần bump version. Hasura configuration drift mặc định BLOCKED để review, không tự drop relationship/source/permission hoặc replace metadata baseline. Naming update có `pg_set_table_customization` preview nhưng chưa tự apply. Không tạo destructive delete chỉ vì một product biến mất khỏi file manifest.

Plan giữ metadata resource version và discovery/manifest hashes như preconditions. Dispatcher phải re-read/revalidate revision/approval trước enqueue và worker phải verify applied state qua P0 services; không replay mù payload preview như một script admin. Relationship stage yêu cầu target đã published/tracked; các product mới có thể cần hai giai đoạn publish rồi reconcile relationships.

## Orchestration và Core v0.2 contract

`ApplicationOnboardingService.plan/import_drafts/apply` yêu cầu VerifiedIdentity, IdentityResolver và OwnershipResolver. Không có provider thì fail closed trước network discovery. `import_drafts` hoàn tất discovery/validation trước ghi; save_drafts là một aggregate port cho atomic Core transaction, không phải một database mới. Receipt chỉ DRAFT/WAITING_APPROVAL; fake repository trả `durable=false`, không báo persistence thật.

`apply` còn yêu cầu durable Core/dispatcher, Core definitions đã persist đúng hash, không BLOCKED, approval hợp lệ. Service chỉ enqueue và chấp nhận receipt QUEUED, **không** báo ACTIVE. Concrete dispatcher hiện chưa có. P1 implementation phải tái sử dụng `SourceRegistrationService`, `DataProductPublisher`, `HasuraPermissionService` và PublicationJobRepository guards hiện hữu; không xây RBAC hay Metadata Client thứ hai.

| Core v0.2 | Adapter cần cung cấp |
|---|---|
| departments/applications | Resolve department reference, kiểm ownership hiện hữu/đích; map application key -> app_key/UUID |
| data_sources | Namespace source_key; map kind/database/username sang db_type/database_name/db_user; secret_ref, TLS, generated hasura_source_name; không lưu DSN/password |
| schema_snapshots | Canonical physical metadata/hash nếu cần lưu; không thêm bảng discovery mới |
| data_products | Resolve source UUID, schema_name/object_name/object_type; draft lifecycle/revision; không coi requested_state là cột mới |
| data_contract_versions | Immutable version, fields (physical column/public name/type/nullable), relationship declarations trong contract_json; dùng physical column để generate permissions |
| access_requests/access_grants | Approval và quyền đọc độc lập, không được tạo từ manifest fields |
| publication_jobs | Atomic enqueue theo target revision/idempotency; relationship/naming work có thể nằm trong PUBLISH_PRODUCT job, không tự thêm job_type/migration |
| audit_events | Actor/owner/correlation + hashes/outcome/idempotency trong cùng Core transaction thích hợp; không raw secrets/DSN |

Aggregate `OnboardingRepository` bổ sung `plan_context`, `save_drafts`, `status`; compose các Core repositories trong cùng unit-of-work để tránh partial registry. `OwnershipResolver.require_owner` trả UUID đã xác minh, không dùng department_key trong JSON làm quyền. `ApprovedPlanDispatcher.enqueue` chịu trách nhiệm revision/approval CAS, durable jobs và audit. Không có transaction chung Core/Hasura.

Gaps cần thống nhất với Core/API team, không sửa migrations: JSON shape của contract field types/aliases; cách lưu và xác minh cardinality evidence (có thể nằm trong contract_json với version review); representation requested publication và relationship job scheduling; discovery freshness và policy chứng chỉ. Typed DTO ở đây không định nghĩa lại 20 bảng Core.

## Kiểm thử và phần việc còn lại

```sh
uv run --locked pytest tests/platform tests/core/hasura tests/core/onboarding -q
uv run --locked ruff check backend tests
uv run --locked ruff format --check backend/app/core/applications backend/app/core/data_sources/discovery.py backend/app/core/hasura_provisioner/planning.py tests/core/onboarding
uv build --no-build-isolation --offline
```

Unit/contract tests dùng SQLAlchemy Inspector fixture, mocked async engine và Core fixtures; đã kiểm cú pháp/schema, keys/refs, FK/composite/manual/remote payload, cardinality, drift, approval dependencies, no-permission generation, draft repeat import và fake-apply rejection. CLI validate/plan được chạy với offline examples. Không có live PostgreSQL/Hasura hoặc persistence thật được kiểm chứng.

Kết quả kiểm tra cục bộ của thay đổi này:

| Kiểm tra | Kết quả |
|---|---|
| Toàn bộ Python unit/contract tests | 210 passed; 1 integration test deselected |
| Live Hasura test khi chưa bật integration | 1 skipped; không gọi engine |
| Ruff check và format check phần onboarding | Passed |
| Lockfile check offline | Passed; không thêm hoặc đổi dependency |
| CLI console entry point | `validate` hợp lệ; offline `plan` trả `ready_for_dispatch=false` đúng blockers |
| Build và kiểm nội dung wheel/sdist | Passed; có code, CLI entry point, schema/examples/docs; không có cache, secret runtime hoặc reference cục bộ |

P1: Core/authorization adapters, durable registry/audit/job transactions, physical validator và runtime grants integration, dispatcher execution/verification cho relationships/custom naming, live engine capability checks, stale-plan/concurrent worker/timeout E2E. Không triển khai Docker/Portal/Keycloak/API endpoints trong phần này. OSS inventory tại [THIRD_PARTY_LICENSES](../THIRD_PARTY_LICENSES.md).

## Tài liệu chính thức

- [SQLAlchemy Inspector/reflection](https://docs.sqlalchemy.org/en/21/core/reflection.html)
- [Hasura same-source relationships](https://hasura.io/docs/2.0/api-reference/metadata-api/relationship/)
- [Hasura remote relationship API](https://hasura.io/docs/2.0/api-reference/metadata-api/remote-relationships/)
- [PostgreSQL remote source relationships, v2.1+](https://hasura.io/docs/2.0/schema/postgres/remote-relationships/remote-source-relationships/)
