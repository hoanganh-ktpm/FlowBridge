# Hasura P0 - Core v0.2 Compatibility and Security Audit

## Kết luận

Module giữ đúng hướng kiến trúc và không cần viết lại. Sau hardening, P0 có projection/guards tương thích với phần source, product, SELECT grant và publication job của tài liệu **DXOS Core Database v0.2**. Chưa đủ điều kiện gọi là tích hợp persistence/authorization/Hasura runtime hoàn chỉnh.

Báo cáo này giữ bằng chứng của lần hardening ban đầu. Các phần HTTP/policy/worker bổ sung sau đó và blockers production hiện hành được cập nhật tại [Core Backend P0](CORE-P0.md); không suy rằng các mục chưa làm trong snapshot này vẫn hoàn toàn thiếu implementation.

Tài liệu đầu vào: `DXOS_Core_Database_v02.pdf`, 29 trang, do nhóm cung cấp; đây là data dictionary đề xuất, không phải migration đã chạy. Đối chiếu trọng tâm trang 3-4, 10, 12-16, 23, 26-29. Không chỉnh sửa PDF, Core models hoặc migration của thành viên khác.

## A. Audit Findings

Line trong bảng là vị trí **trước lần audit này**, để ghi lại bằng chứng lỗi ban đầu; hàm/class được giữ tên để tra bản đã sửa.

| Severity | File:Line | Finding | Evidence | Fix |
|---|---|---|---|---|
| High | `backend/app/core/hasura_provisioner/services.py:131` | Một grant lỗi dẫn đến drop toàn permission, kể cả grants khác hợp lệ | Regression `test_invalid_grant_does_not_remove_another_valid_grant` thất bại trước sửa: 0 permissions thay vì 1 | Validate từng grant, hợp nhất cột hợp lệ, chỉ grant lỗi vào ERROR; preserve quyền còn hợp lệ |
| High | `backend/app/core/hasura_provisioner/services.py:131` | Approval bị dùng thay trạng thái publish; product READY có thể nhận permission | Regression `test_approved_but_unpublished_product_cannot_receive_permissions` không raise ở bản cũ | Kiểm source CONNECTED/product ACTIVE trước apply; publish và approval là các trạng thái khác nhau |
| High | `backend/app/core/hasura_provisioner/client.py:260` | Update permission đọc snapshot cũ nhưng drop lại dùng snapshot mới, có thể xóa thay đổi concurrent | Regression `test_replacement_does_not_delete_concurrently_changed_permission` không conflict ở bản cũ | Drop dùng nguyên resource_version đã đọc, rồi đọc lại trước create; không refresh guard để xóa state mới |
| High | `backend/app/core/hasura_provisioner/services.py:54` | Allowlist host chưa ràng buộc database/user/port/TLS của secret với cấu hình Core | DTO cũ không có các trường kết nối ở trang 10; URI cùng host nhưng database/user khác vẫn qua được | Bổ sung projection connection tuple, exact match sau decode URI và regression cho từng trường |
| Medium | `backend/app/core/hasura_provisioner/schemas.py:106` | Grant chỉ có revision chung, thiếu desired/applied distinction của Core v0.2 | Regression kiểm `SelectGrant.model_fields` thất bại trước sửa | Tách desired/applied revision, invariant ACTIVE, giữ input/property revision cũ làm alias |
| Medium | `backend/app/core/hasura_provisioner/services.py:34` | Chưa có Published status proof; grant đang bị buộc theo active contract thay vì version đã pin | DTO cũ chỉ trả product/id/columns; trang 13 và 15 yêu cầu PUBLISHED/pinned version | Validator nhận optional contract_id; kiểm PUBLISHED và đúng product; cache từng version trong một reconcile |
| Medium | `backend/app/core/hasura_provisioner/ports.py:39` | Job interface thiếu claim/lease/attempt fencing và expected revision ở entry points | Không thể diễn đạt lease/reclaim/conditional finish theo trang 16 | Bổ sung job projection/Protocols và optional expected revision guards; chưa xây worker |
| Medium | `tests/core/hasura/fakes.py:25` | Fake chưa kiểm auth header/version, duplicate track/permission; grant ACK không CAS | Mock có thể chấp nhận metadata hoặc acknowledgement không thực tế | Siết fake HTTP, fake state/expiry/revision CAS; test timeout sau commit, cancellation, stale job, mixed roles |
| Low | `backend/app/core/hasura_provisioner/client.py:104` | Error code không phải string có thể gây TypeError thay typed error | Membership vào set với `code=[]` không hash được | So sánh tuple và negative tests cho HTTP 200/400 với malformed code |

Bốn regression đầu tiên về aggregation, unpublished product, concurrent replacement và revision projection được chạy **trước sửa**: 4 failed. Các sửa khác có tests hành vi tương ứng, không dùng số lượng tests làm bằng chứng duy nhất.

## B. Thay đổi có giới hạn

| File | Lý do |
|---|---|
| `backend/app/core/hasura_provisioner/schemas.py` | Connection/status projections, published contract, scope_json, desired/applied revisions, typed job; không phải ORM |
| `backend/app/core/hasura_provisioner/services.py` | Lifecycle/revision guards, từng pinned contract, preserve valid grants, CAS acknowledgement/failure |
| `backend/app/core/hasura_provisioner/client.py` | Sửa TOCTOU khi thay permission và malformed error code; giữ nguyên endpoint/operation set |
| `backend/app/core/hasura_provisioner/ports.py` | Tối thiểu hóa khoảng trống Core adapters/job lease contracts |
| `tests/core/hasura/fakes.py`, `conftest.py` | State và request assertions sát contract hơn |
| `tests/core/hasura/test_hardening.py`, `fixtures/core-v02.json` | Regression tests và projection fixture theo data dictionary |
| `tests/core/hasura/test_services.py` | Bỏ giả định grant bắt buộc dùng active contract; thay assertion mutation quá yếu |
| Module README, báo cáo này và license inventory | Ghi mapping, public interface adjustments, scope và blockers |

Giữ module, service names và public Metadata Client methods. Service thêm keyword arguments tùy chọn cho expected revision. Source projection có trường kết nối bắt buộc; validator thêm optional `contract_id`; grant repo thêm failure acknowledgement; Job Protocol thêm lease/attempt fencing. Đây là các thay đổi boundary có lý do tương thích v0.2, không phải cam kết zero-change cho một adapter chưa triển khai.

Không thêm dependency, API router, database, migration, framework, worker hoặc hạ tầng. `PublicationJobRepository` vẫn là P1 port được yêu cầu, không giả là code đang chạy. Không có circular import được phát hiện; Platform không import Core. Bỏ hướng viết lại client/DTO/repository framework vì không cần thiết. Tách kiểm contract/effective grants thành helper nhỏ, tránh lặp validate cùng version; không cache quyết định quyền giữa các request.

## C. Kiểm thử thực tế

| Mốc | Kết quả |
|---|---|
| Baseline chạy lại trước audit | 106 passed, 1 deselected; 36 Platform + 70 Hasura |
| Tái hiện lỗi trước sửa | 4 regression cases failed như dự kiến |
| Sau hardening | 147 passed, 1 deselected; 36 Platform + 111 Hasura |
| Live smoke test riêng | 1 skipped: chưa opt-in/cấu hình Hasura |
| Ruff lint và formatter | Đạt trên source/tests liên quan |
| Lockfile / source distribution / wheel | Đạt kiểm tra/build offline; không thay dependencies |

Lệnh từ root repository:

```sh
uv run --locked pytest tests/platform tests/core/hasura -q
uv run --locked pytest tests/core/hasura/test_live.py -m integration -q
uv run --locked ruff check backend tests
uv run --locked ruff format --check backend/app/core/hasura_provisioner tests/core/hasura
uv lock --check --offline
uv build --no-build-isolation --offline
```

Trên môi trường Windows sandbox hiện tại, async tests được chạy ngoài sandbox bằng interpreter `.venv/Scripts/python.exe`; toàn bộ HTTP là MockTransport, không dùng network thật. Một negative case cũ coi mọi contract khác active là sai đã được thay bằng test riêng cho valid/invalid pinned contract, vì giả định cũ không đúng v0.2.

Phân loại: tests client/services/hardening là unit tests; các assertion fixtures Hasura/Core là **offline contract checks**, không phải chứng nhận live API. Mock có kiểm version, auth header, duplicate role/table, resource version và SELECT payload; vẫn không thay được engine/parser/PostgreSQL thật. Không có static type checker được cấu hình, không tự thêm công cụ mới.

## D. Dependency graph và persistence

```text
Future authenticated Router / publication worker (chưa có)
  -> SourceRegistrationService / DataProductPublisher / HasuraPermissionService
       -> IdentityResolver: xác minh thẩm quyền quản trị, thiếu thì deny
       -> Core Repository Protocols: desired state + conditional acknowledgement
       -> SecretResolver / DataContractValidator: trusted runtime/schema boundary
       -> HasuraMetadataClient -> injected HTTPX AsyncClient -> /v1/metadata
```

| Nơi lưu | Nội dung |
|---|---|
| Core DB | applications/sources/products/contracts/requests/grants/jobs/audit; schema do nhóm Core sở hữu |
| Business DB | Dữ liệu nguồn, transaction và business authorization thuộc từng ứng dụng |
| Hasura Metadata DB | Internal metadata/catalog do engine quản lý; không có ORM/Alembic trong module này |
| Git | Source, docs, typed fixtures tổng hợp và baseline metadata đã review; không chứa runtime DSN/export/secrets |
| Tests | Fake repository và fake Hasura in-memory; không có persistence thật |

Không có public admin endpoint hay GraphQL read proxy trong module. Admin control-plane secret không đi đến runtime caller. `approved`, `.table` và aliases là projection policy/adapter, không đề nghị thêm cột vào Core DB.

## E. P1 Integration Contract

### Source và product

- Source: id, hasura_source_name, host/port/database_name/db_user/ssl_mode/db_type, secret_ref, status, revision; approval tính từ trusted policy/application/department hiện hành. `source_key` không được nhầm thành Hasura name.
- Product: id, data_source_id, schema_name/object_name -> TableRef, active_contract_id, status, revision; chỉ publication targets có contract. Adapter phải bảo đảm object_type/physical schema phù hợp.
- `get` trả projection đã validate; mark_connected/mark_active phải CAS revision và current lifecycle state, thất bại raise `MetadataConflict`. Cần giải pháp serialize update với in-flight provisioning khi xây adapter thật.

### Grants, contracts và identity

- Grant: id, data_product_id, contract_version_id, consumer_department_id, consumer_principal_id=null, scope_json SELECT/columns/ALL, status, desired_revision, applied_revision, expires_at; approval đã kiểm quyền chấp thuận. Không nhận enforcement_role từ caller, role do backend sinh và adapter lưu/đối soát.
- Core v0.2 partial UNIQUE giới hạn một live grant cho mỗi product/subject. Tests multi-live kiểm aggregation phòng thủ; fixture một-live/terminal-history phản ánh schema. Không đổi constraints của nhóm Core để chạy test fixture.
- Repository: get theo ID, list toàn target, lock liên tiến trình; mark_applied/revoked/failed có CAS. mark_applied kiểm approval/status/expiry và desired_revision trước ACTIVE; mark_revoked không xóa lịch sử hay mất EXPIRED; mark_failed ghi ERROR cho grant bị loại sau khi Hasura đã được đối soát.
- Validator nhận optional contract_id: kiểm contract đúng product, PUBLISHED, immutable version, columns/types và schema nguồn thực. Không tự thay version cũ bằng latest. Một version chỉ validate một lần mỗi reconcile.
- IdentityResolver chỉ cho phép actor đã xác minh và đủ quyền quản trị; không suy quyền từ headers/department_id do client tự khai. Owner approval, membership validity và authorization của từng request đọc nằm ở Core/Identity boundary, chưa có implementation thật.
- Row scope ngoài ALL, principal grants, arbitrary SQL/filter/admin metadata commands không được nhận vào MVP. Principal thuộc nhiều department không được tự union thành một role toàn quyền; runtime chọn role trong allowed roles đã xác minh.

### Jobs và crash/retry

- Enqueue với đúng target FK, type, target_revision, idempotency key và actor; cùng transaction thay đổi desired state.
- claim/renew/finish theo RUNNING + owner + attempt_count + lease chưa hết hạn; finish kiểm target revision hiện tại. Job bị reclaim hoặc revision cũ không được acknowledge thành công.
- Worker truyền expected_revision vào source/product/revoke; reconcile truyền expected_grant_id cùng expected_revision. Không đổi revision của job cũ để chạy theo desired state mới.
- Timeout/network failure khi mutate là **outcome chưa biết**: có thể engine đã commit. Lần thử sau đọc metadata thực rồi reconcile; tests đã mô phỏng response mất sau add/track/create/drop. Không retry HTTP mutation vô điều kiện.
- Bounded exponential backoff/jitter, max_attempts, recovery lease và audit events thuộc worker/Core adapters P1. Cancellation được truyền lên; không báo thành công khi task bị hủy. Không có retry engine hoặc broker mới.

## F. Remaining Risks / Deployment Blockers

| Severity | Chưa xử lý trong P0 | Bước kiểm chứng trước vận hành |
|---|---|---|
| High | Chưa có trusted admin authorization, Hasura JWT/webhook/read boundary; long-lived role có thể bỏ qua expiry/revoke mong muốn | Tích hợp Identity/Core checks mỗi request; thử forged role/department, disabled membership, expiry/revoke và cache TTL trên request thật |
| High | Chưa có distributed Core locks/transactions/CAS adapters và worker lease persistence | Test PostgreSQL thật với hai workers, stale job, lease reclaim, desired update trong lúc HTTP, crash sau external commit và trước acknowledgement |
| High | `from_env` phía Python và Hasura có thể khác nhau; export metadata không chứng minh engine đọc đúng database/user | Cấp và đối chiếu runtime bindings, kiểm physical schema/query trên engine test chuyên dụng; không quảng cáo dynamic credential provisioning sẵn sàng |
| High | Chưa kiểm engine thật, database grants/RLS, schema validator thật, Core partial UNIQUE | Apply fixtures trên Hasura v2 CE + disposable DB; kiểm columns, filtered view, denial Logistics, update-commit/read và revoke |
| Medium | Drop/create permission không atomic; lỗi sau drop gây gián đoạn quyền hợp lệ | Fault injection, retry/reconcile; không restore scope bị thu hồi để tránh over-grant |
| Medium | Source/product có thể đổi desired state khi HTTP đang chạy; precheck/CAS không hoàn tác external effect đã xảy ra | Serialize config changes và jobs bằng adapter lock; fail closed ở read authorization và reconcile revision mới |
| Medium | Audit/job acknowledgement chưa persistent và có thể lỗi sau Hasura commit | Core adapter transaction + idempotent audit event_key; replay không trùng sự kiện, đối soát sau restart |
| Low | Identifiers MVP chỉ lowercase ASCII; error diagnostics có chủ ý bị lọc | Chốt convention schema/views; nếu cần mở rộng, thêm schema validation và sanitize diagnostics thay vì log raw DSN/body |
| Medium | Host allowlist chưa thay thế network egress/DNS policy của engine | Kiểm DNS resolution, firewall và quyền kết nối ở bước triển khai; không coi input validation là chống SSRF toàn diện |

Không phát hiện Critical issue trong phạm vi source P0 được xem xét. Các High phía trên được ghi nhận công khai là điều kiện chặn production, không coi chúng đã giải quyết bằng mocks. Chưa có phép kiểm query permission runtime thật hoặc end-to-end xuyên Core/Hasura/Identity.

## G. Definition of Done cho lần audit

- [x] Tái hiện và sửa các lỗi P0 đã nêu; có negative/regression assertions.
- [x] Không đổi database schema/migration; không thêm dependency hoặc infrastructure.
- [x] Không đưa credential, DSN thật hoặc test data nhạy cảm vào source.
- [x] Không nhận trusted role từ payload; không cấp mutation, aggregation hoặc subscription.
- [x] Thu hồi/loại grant không xóa quyền hợp lệ khác; scope ngoài MVP bị từ chối.
- [x] Public service/client names được giữ; các thay đổi boundary có lý do và mapping rõ.
- [x] Kết quả unit/contract/skip được tách biệt; không tuyên bố live integration.
- [ ] Production integration: hoàn tất các High blockers ở mục F và chạy E2E thật.

## Tham chiếu chính thức

- [Metadata API / resource version](https://hasura.io/docs/2.0/api-reference/metadata-api/index/)
- [Postgres sources](https://hasura.io/docs/2.0/api-reference/metadata-api/source/)
- [Table/view tracking](https://hasura.io/docs/2.0/api-reference/metadata-api/table-view/)
- [SELECT permissions](https://hasura.io/docs/2.0/api-reference/metadata-api/permission/)
- [Export/reload/consistency](https://hasura.io/docs/2.0/api-reference/metadata-api/manage-metadata/)
- [Pinned v2.48.16 Metadata implementation](https://github.com/hasura/graphql-engine/blob/v2.48.16/server/src-lib/Hasura/Server/API/Metadata.hs): đã đọc trực tiếp đoạn resource-version persistence guard; không chỉ suy luận từ tên field.
- [License inventory](../THIRD_PARTY_LICENSES.md): versions/package metadata được kiểm lại; không phát sinh runtime dependency mới.
