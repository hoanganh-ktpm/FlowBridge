# Core Backend P0

**Mở rộng hiện tại:** source head `core_0005` bổ sung registry quan hệ, worker local/remote provisioning và structured nested query theo grants; chưa chạy migration hay E2E nhiều nguồn trên Supabase. Xem [Relationship Queries](RELATIONSHIP-QUERIES.md) để phân biệt implementation, kiểm thử offline và điều kiện nghiệm thu thật.

Runtime cho một vertical slice Data Sharing, theo API Contracts 0.2.0 hiện hành. Không thay endpoint/DTO baseline, không tạo ORM/migration và không triển khai Agent, Workflow hoặc Business APIs.

**Cập nhật sau milestone:** Core head core_0004 theo 11 bảng P0 + command receipts. PostgreSQL adapter và native composition app.core.runtime đã triển khai; profile [Docker Integration & Real E2E](DOCKER.md) kiểm Data Sharing với Keycloak/Hasura và Supabase thật. Command/worker giữ SERIALIZABLE/CAS; GET resource/status dùng transaction read-only không giữ row lock để tránh ảnh hưởng provisioning. Bảng khảo sát, bằng chứng offline và checklist bên dưới giữ bối cảnh milestone runtime ban đầu, không phải toàn bộ trạng thái hiện tại. [Automated Onboarding](AUTOMATED-ONBOARDING.md) ghi nhận thêm milestone native trước đó.

## Trạng thái

| Thành phần | Khảo sát đầu milestone | Kết quả |
|---|---|---|
| FastAPI composition/router | MISSING | IMPLEMENTED & TESTED OFFLINE; `backend/app/main.py`, `backend/app/core/router.py` |
| Manifest parser, discovery, compiler | IMPLEMENTED/PARTIAL | Tái sử dụng; compiler dùng IT authority, DRAFT editable và không yêu cầu consumer grant để cấu hình |
| Canonical onboarding | PARTIAL | Validate, STATIC/LIVE plan và atomic draft import qua application service |
| Core persistence | MISSING/BLOCKED | Protocols và transaction boundary có implementation sử dụng chúng; PostgreSQL adapter BLOCKED BY DATABASE |
| Identity | Verifier IMPLEMENTED; principal policy MISSING | Introspection được tái sử dụng; kiểm trusted principal binding, role, membership/application và grant mỗi request |
| Provisioning | Client/services IMPLEMENTED; worker MISSING | Worker claim/lease/attempt/retry/CAS, dùng lại các services và Metadata Client |
| Live data reader | MISSING | HTTPX GraphQL adapter không dùng admin secret, scalar validation và pinned mapping; NOT INTEGRATED với auth profile thật |
| Durability/concurrency/enforcement thật | BLOCKED | DEFERRED tới khi có PostgreSQL, Keycloak và Hasura v2 CE được cấu hình |

`IMPLEMENTED & TESTED OFFLINE` không đồng nghĩa đã chạy E2E hoặc có persistence production. Các test doubles chỉ nằm trong `tests/core/runtime/`.

## HTTP handlers đã triển khai

Các path bên dưới có prefix `/api/v1/core`. Đây là tập con 25 operations của baseline, không phải toàn bộ 48 P0 hoặc 88 operations.

| Method | Path | Công việc |
|---|---|---|
| GET | `/me` | Danh tính đã xác minh và quyền/bindings Core hiện hành |
| POST | `/applications` | Đăng ký application DRAFT |
| PATCH | `/applications/{application_id}` | Cập nhật metadata/trạng thái bằng revision |
| POST | `/data-sources` | Đăng ký nguồn, kiểm tuple với operator secret binding và scope |
| GET | `/data-sources/{data_source_id}` | Metadata nguồn đã redaction |
| POST | `/data-sources/{data_source_id}/connect` | Ghi VALIDATING và CONNECT_SOURCE job; trả 202 |
| GET | `/data-sources/{data_source_id}/schema` | Bounded live schema discovery, không đọc business rows |
| POST | `/data-products` | Tạo Data Product DRAFT |
| GET | `/data-products/{data_product_id}` | Metadata product; consumer chỉ thấy catalog hiện hành |
| POST | `/data-products/{data_product_id}/contracts` | Tạo canonical contract DRAFT |
| GET | `/data-contracts/{contract_version_id}` | Contract; consumer chỉ thấy version hiện hành |
| PATCH | `/data-contracts/{contract_version_id}` | Sửa DRAFT; chặn PUBLISHED/RETIRED |
| POST | `/data-contracts/{contract_version_id}/publish` | Kiểm schema, key evidence và scalar profile trước khi publish |
| POST | `/data-products/{data_product_id}/publish` | Pin contract và enqueue PUBLISH_PRODUCT; chưa trả ACTIVE |
| POST | `/data-products/{data_product_id}/disable` | Deny trước, enqueue đối soát permissions |
| POST | `/access-requests` | IT tạo hồ sơ thay consumer department |
| GET | `/access-requests/{access_request_id}` | IT đọc hồ sơ |
| POST | `/access-requests/{access_request_id}/approve` | Request/grant/job/audit/receipt cùng transaction |
| GET | `/access-grants/{access_grant_id}` | IT hoặc consumer thuộc department tương ứng |
| POST | `/access-grants/{access_grant_id}/revoke` | Tăng desired revision, ghi REVOKING/job; deny ngay |
| GET | `/publication-jobs/{job_id}` | IT đọc trạng thái đã redaction |
| GET | `/data-products/{data_product_id}/rows` | Đọc mới, một grant bao trùm toàn bộ projection |
| POST | `/application-onboarding/validate` | Kiểm manifest/canonical annotations, không discovery/write |
| POST | `/application-onboarding/plans` | STATIC unknown hoặc LIVE evidence, diff đã redaction |
| POST | `/application-onboarding/imports` | Recompile LIVE, kiểm hash/revision, atomic draft import |

List/cursor APIs, principal/membership administration, request reject/cancel và phần P0 ngoài bảng chưa triển khai trong milestone. Không đăng ký endpoint placeholder trả thành công.

## Composition và cách chạy

`app.main.create_app(CoreDependencies(...))` là composition boundary. Nhóm tích hợp cung cấp:

- `CoreStore` production có `durable=True`, không phải `MemoryStore` đổi cờ.
- `SourceDiscoveryService(PostgresSchemaDiscovery(...), ApprovedSourcePolicy(...))` với scope table/view được duyệt theo secret reference.
- `IdentitySettings` và HTTPX client có TLS/timeout phù hợp cho introspection.
- `HasuraMetadataClient`, `HasuraSettings` và cùng các from_env bindings đã được operator cài trong Hasura.
- `HasuraDataReader` với `HasuraCredentialProvider` thật; auth/naming profile phải được kiểm chứng trước khi bật `verified_auth_profile`.
- `PublicationEvidence` thật nếu công bố view không có PK/global UNIQUE evidence.

Native wiring hiện nằm tại `app.core.runtime:create_app`; chạy API/worker theo [hướng dẫn integration](AUTOMATED-ONBOARDING.md). Gọi `app.main.create_app()` không có adapters vẫn từ chối, không khởi tạo database/memory store. `testing=True` chỉ dùng khi chủ động xây app trong test; không có environment switch tự bật fake mode.

Composition sở hữu vòng đời HTTPX clients: đóng chúng trong lifecycle của deployment. Không dùng client có credential của người dùng làm Metadata Client. Không có default user, token, credential hoặc route bypass xác thực.

Worker được compose bằng `PublicationWorker(sharing_service, metadata_client, settings)`, gọi `await worker.run_once(worker_id)` từ worker process được quản lý. Process supervisor thực hiện polling/backoff khi không có việc và graceful cancellation. Worker không được kích hoạt bằng FastAPI BackgroundTasks hoặc in-process queue thay persistence.

## Persistence handoff

Interface tại `backend/app/core/db/ports.py` là **projection/unit-of-work boundary**, không phải schema lưu JSON cho mọi bảng. Adapter phải map tường minh sang tables/models được nhóm DB bàn giao; không tự tạo bảng `resources`, cột `public/private`, hoặc suy rằng các dict này là ORM.

| Projection | Tables/reference cần bàn giao |
|---|---|
| PrincipalBinding/PrincipalContext | principals, department_memberships, departments; issuer/subject/kind/service_client_id phải khớp VerifiedIdentity; application_id có thể NULL cho core service |
| application/source | applications, data_sources; giữ connection tuple/secret_ref nội bộ, trả DTO nguồn đã redaction |
| product/contract | data_products, data_contract_versions; canonical contract_json nguyên vẹn, version/product identity và published content bất biến |
| request/grant | access_requests, access_grants; department-only SELECT/ALL, scope là physical columns |
| job | publication_jobs: target/type/revision, idempotency_key, requested_by, lease/attempt/retry |
| audit | audit_events append-only; actor/initiator, action, resource, outcome/error, request_id và content hash; không raw input/token/coordinates/rows |
| HTTP receipt | Khoảng trống persistence đã có trong baseline: namespace, body hash, status/body/location và retention tối thiểu 7 ngày; không dùng job key thay HTTP replay |

`StoredResource.private` gồm projection nội bộ, không đồng nghĩa cần thêm cột:

- Source: host/port/database_name/db_user/ssl_mode/secret_ref từ data_sources.
- Job: idempotency_key/requested_by/locked_by/lease_expires_at từ publication_jobs. correlation_id phải được hydrate bền vững từ enqueue audit `request_id` theo job resource_ref, hoặc nhóm DB chốt lưu riêng. Runtime không giả định cột đó đã có.
- Principal resolver trả `{issuer, subject, kind, service_client_id, context}` từ trusted records; tuyệt đối không từ request headers/groups tự khai.

### Transaction/locking requirements

1. `transaction()` là serializable unit-of-work, hoặc dùng explicit aggregate locks + CAS tương đương. `replace()` không phải blind UPDATE. Đọc resource được đổi, grant approval, contract switch, disable và HTTP receipt phải được serialize đúng graph. Concurrent key reservation/uniqueness phải được DB bảo đảm, không chỉ check-then-insert trong Python. `transaction(read_only=True)` dành cho GET resource/status: snapshot REPEATABLE READ, READ ONLY, không FOR UPDATE và không dùng để thực hiện command.
2. Atomic commands: desired-state changes + request decision/grant + job + audit + HTTP receipt cùng commit/rollback. Import lưu tất cả drafts hoặc không lưu gì; bảo toàn resource active không đổi và chặn active drift.
3. HTTP namespace ràng buộc verified issuer/subject, method, canonical path và key. Replay phải resolve quyền hiện hành trước khi trả receipt; payload khác ->409. Completed replay không chạy lại revision guard cũ.
4. `claimable_jobs(now,limit=10)` phải dùng bounded indexed query với `FOR UPDATE SKIP LOCKED` hoặc cơ chế tương đương, bao gồm RUNNING hết lease. Thời gian từ DB; claim/renew/finish kiểm owner + attempt + expiry. Cần indexes cho due/reclaim và unique target/type/revision job key.
5. `permission_lock(product,department)` là lock chung giữa các worker/process, không phải asyncio.Lock production. Giữ ownership suốt metadata reconciliation nhưng không giữ một Core DML transaction quanh các Hasura mutations. Fenced acknowledgements dùng short transactions riêng.
6. Public desired/applied revision và trạng thái chỉ ACK sau read-back/consistency verification. Expiry, disabled entity và REVOKING deny ở read policy trước khi engine reconciliation xong.
7. FKs, unique keys, immutable contract version và một live department grant cho product phải được kiểm trong schema/transaction. Adapter chuyển uniqueness/serialization conflicts sang lỗi Core đã chuẩn hóa; không trả lỗi SQL/DSN thô.
8. Bootstrap departments, principals, IT role và service client bindings là operator/database handoff, không được tự cấp theo token group. Runtime không tự provision tài khoản hay chạy migration.

Tại milestone runtime ban đầu, các bảo đảm SQL trên chỉ được mô phỏng bởi FakeStore. Milestone onboarding bổ sung PostgreSQL integration cho transaction/rollback/receipts/restart/advisory locks và concurrent claims/lease fencing; chưa thay kiểm deployment privileges, crash sau external commit hoặc engine enforcement thật.

## Read/auth profile và giới hạn

- IT là control-plane admin, không tự có row grant. STAFF/OWNER chỉ là metadata tổ chức. Service account không tự là admin; service của ứng dụng phải có client binding và application/department còn ACTIVE.
- Mỗi read resolve principal/membership lại, kiểm source/application/department/product/current contract và chọn **một** grant ACTIVE chưa hết hạn, applied=desired, đủ physical columns. Không union nhiều grant để mở rộng projection.
- HTTP columns và row keys là public names; query/permission dùng mapping từ immutable contract. Metadata publication áp dụng column customization bằng CAS, giữ source namespace; query server-generated dùng GraphQL aliases để kiểm lại physical row projection.
- Existing column/root customization khác mapping được duyệt trả metadata conflict, không tự ghi đè cấu hình ngoài quyền sở hữu đã xác minh. Đổi mapping của bảng đã customize cần operator review/reconcile trước khi chạy lại publication; chưa có metadata-review UI trong milestone.
- Sort theo business key, limit+1, kiểm cả hàng dư trước khi trả has_more. Null/type/constraint/precision sai hoặc GraphQL partial errors ->503, không trả phần dữ liệu hợp lệ còn lại. Mỗi request gửi query mới, không cache hàng dữ liệu.
- P0 publication hiện hỗ trợ text/UUID/character, integer/smallint, boolean, real/double precision, date và timestamptz với wire types tương ứng. Bigint/numeric chưa có proof precision tổng quát: dùng view tạo text được duyệt hoặc bị chặn; không ép kiểu/round ngầm. Structured values/wire_schema chưa publish trong profile này.
- Pattern constraints chỉ chạy với profile tuyến tính: anchored literal prefix/suffix và một character class có tối đa một quantifier, ví dụ `^SKU-[A-Z0-9]+$`. Pattern phức tạp vẫn giữ trong draft nhưng chặn publication/read tới khi có validator an toàn phù hợp; không chạy regex backtracking tùy ý trên dữ liệu nguồn.
- Views cần uniqueness evidence provider đáng tin cậy, ràng buộc product/columns/schema hash và thời hạn. Chưa có provider production. Relationship definitions được giữ trong canonical drafts và compiler preview; publication/dispatch relationships vẫn bị chặn cho tới khi có runtime capability/evidence integration. Không coi preview payload là cấu hình đã áp dụng.
- Read transport chỉ nhận credential do trusted provider cấp theo TrustedHasuraContext. Nó không gửi admin secret, caller role header hoặc arbitrary GraphQL. [Hasura v2 hỗ trợ JWT/webhook authentication](https://hasura.io/docs/2.0/auth/authentication/index/); phải kiểm issuer/audience/claims hoặc hook policy trên deployment thật, không chỉ đặt cờ verified.
- Hasura Metadata API phải private; GraphQL ingress phải chặn truy cập trực tiếp không có policy tương đương. Nếu không, token department có thể đi vòng qua current Core checks. Internal short-lived JWT provider đã có unit tests; auth profile/ingress thật chưa được nghiệm thu và mặc định chưa bật.
- New reads bị revoke/expiry/disable từ chối ngay; in-flight requests có thể hoàn tất trong timeout. Chưa có expiry sweep scheduler sau các job đã SUCCEEDED; expiry vẫn deny ở Core, engine cleanup định kỳ là integration backlog.

## Kiểm thử và nghiệm thu

### Baseline capacity gap cần review riêng

Manifest v1 `properties.sources` chỉ có minItems, chưa có maxItems, trong khi `application-onboarding.yaml` giới hạn ImportReceipt.data_source_ids ở 100, plan steps/issues ở 1000. Không sửa contracts trong milestone này. Runtime hiện trả 503 cho plan/import vượt profile 100 sources/products hoặc 1000 steps/issues, thay vì truncate, ghi một phần hoặc trả response trái schema. Giới hạn wire/profile cuối cùng cần nhóm contracts xác nhận trước khi hỗ trợ batch lớn; static validation không đồng nghĩa đủ năng lực import.

```sh
uv run --locked pytest tests/core/runtime -q
uv run --locked pytest -q
uv run --locked ruff check backend tests
```

HTTP/component tests gọi FastAPI handlers, introspection verifier, application services, policy, worker, Metadata Client và GraphQL adapter thật bằng injected repository/HTTP doubles. Response status/shape/headers được validate với baseline OpenAPI, không mock toàn bộ endpoint trả JSON cố định. Thay đổi dữ liệu nguồn trong test chỉ là thay fixture, **không chứng minh fresh-data visibility sau PostgreSQL commit**.

Bằng chứng offline ngày 23/10/2026: toàn bộ suite **685 passed, 1 integration deselected**; riêng runtime P0 **90 passed**. Ruff lint/format và `git diff --check` đạt. Build source distribution/wheel offline đạt; kiểm archive không chứa cache, archive cũ, local checkpoint hoặc `.env` thật. Bộ contracts và dependency lock không thay đổi. Chưa chạy bất kỳ acceptance engine/database thật nào bên dưới.

DEFERRED acceptance, thực hiện theo thứ tự:

1. Nhóm DB bàn giao models/migrations và PostgreSQL adapter; review transaction/lock/idempotency/audit mappings, sau đó chạy migrations trên môi trường thử được cho phép.
2. Nối Keycloak thật; kiểm wrong issuer/audience/client, disabled principal/membership/application và IT không có department membership.
3. Cấu hình source reader least privilege, secret/from_env và scope; nối Hasura v2 CE, kiểm publication aliases và column permissions thực tế.
4. Kiểm provider JWT/webhook và ingress không thể bypass; thử role/department giả, grant thiếu cột, không có implicit IT row access.
5. Chạy luồng Warehouse register/connect -> publish -> cấp Sales -> read; commit thay đổi ở Warehouse DB -> query mới thấy giá trị mới -> revoke -> request mới 403 trước khi worker hoàn tất.
6. Dùng ít nhất hai worker processes: claim đồng thời, reclaim/attempt fencing, revision thay giữa HTTP, crash sau external commit trước ACK, retry/terminal failure và bảo toàn grants khác. Kiểm HTTP replay qua restart, concurrent keys, rollback/audit và retention.

Chỉ sau những bước này mới ghi nhận Data Sharing E2E và enforcement thật. Không có Docker deployment, migration execution, commit hoặc push trong milestone triển khai này.
