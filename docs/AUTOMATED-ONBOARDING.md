# Automated onboarding — native PostgreSQL integration

**Runtime hiện hành:** [Docker Integration & Real E2E](DOCKER.md) dùng đúng hai container Keycloak/Hasura, Core/worker native Windows và Supabase hiện có. Không cần JDK portable hoặc database mới. Tài liệu này ghi nhận milestone native và harness database cô lập trước đó; các blocker/matrix lịch sử bên dưới không mô tả trạng thái profile hiện hành. Evidence mỗi lượt tích hợp được lưu trong ledger local, không suy PASS từ kết quả lịch sử.

## Phạm vi

Core P0 có PostgreSQL CoreStore adapter và native FastAPI/worker composition. Không tạo Dockerfile/Compose/container, không migrate khi startup, không có memory store fallback. Không đổi baseline API Contracts hoặc schema Core để triển khai milestone này.

| Module | Đã có | Bổ sung |
|---|---|---|
| Core schema/API services | core_0004, router/policy/commands | PostgreSQL unit-of-work, projection và durable receipts |
| Identity | OIDC introspection, principal policy | Resolve current principal/membership từ PostgreSQL |
| Onboarding | Manifest 1.0/canonical contracts, compiler | Draft import chạy thật với PostgreSQL/discovery, không cần Hasura để lưu draft |
| Worker | Hasura reconciliation và leases | PostgreSQL SKIP LOCKED, CAS, session advisory locks và bounded transient retries |
| Data reads | Hasura GraphQL reader | Internal short-lived HS256 credential provider, mặc định tắt tới khi xác minh auth/ingress |

## Persistence

Adapter nằm tại backend/app/core/db/postgres.py, map tường minh các bảng hiện hành, không tạo resource/EAV table. PostgreSQL SERIALIZABLE và row locks bảo vệ unit-of-work; mutation, job, audit và receipt cùng transaction. Không tự retry xung đột revision nghiệp vụ. Chỉ serialization/deadlock có retry giới hạn ở command và worker claim/failure acknowledgement. Sau đó vẫn trả conflict hoặc chờ worker poll/reclaim, không giả thành công.

Job query bounded, có FOR UPDATE SKIP LOCKED; claim/renew/ACK dùng owner/attempt/lease/revision fencing. Session advisory lock dùng connection riêng, không giữ Core DML transaction quanh Hasura HTTP. Dùng direct connection hoặc session pooler; transaction pooler 6543 bị từ chối. NullPool giới hạn state leakage; PostgreSQL statement/lock timeouts hữu hạn. SQL exceptions được chuyển sang lỗi Core chuẩn, không đưa DSN/driver parameters vào HTTP.

Owner Department của Product suy qua source/application. Source connection fields và job lease fields là private projection, không trả trong public DTO. Grant enforcement role tính từ department được xác minh, không nhận từ caller. Identity/membership/grant được đọc lại ở mỗi request; replay receipt vẫn kiểm quyền hiện hành trước.

## Draft onboarding và approval

POST /api/v1/core/application-onboarding/validate dùng manifest/canonical schemas hiện hành. LIVE plans thực hiện schema discovery thật qua operator-approved secret/host/object scope. Không suy nghiệp vụ từ tên cột.

Nếu chưa cấu hình Hasura Metadata Client, LIVE evidence xác nhận PostgreSQL schema, metadata_resource_version=NULL và các bước engine giữ HASURA_STATE_UNKNOWN/BLOCKED. Plan luôn ready_for_dispatch=false trong runtime P0. Draft import vẫn recompile schema/plan/hash/revision rồi commit nguyên tử; không tạo grant/job, không track bảng hoặc publish. Nếu đã cấu hình client nhưng Hasura lỗi, lỗi không được thay bằng metadata giả.

IT/PLATFORM_ADMIN là approval path hợp lệ theo contracts hiện hành: publish contract/product và approve access request là lệnh quản trị có audit. Không đòi Workflow Engine duyệt lại, cũng không tự gọi approval khi nhập manifest. Membership OWNER/STAFF không cấp quyền này. SSO hoặc Workflow chưa sẵn sàng không cho phép bỏ qua quyền IT.

## Chạy trực tiếp, không Docker

```sh
python -m pip install uv==0.13.1
uv sync --locked --group dev --group database --group build
```

Tạo .env.test cục bộ, không commit; không ghi đè .env đang dùng. Điền FLOWBRIDGE_CORE_DATABASE_URL trỏ **database kiểm thử riêng** và các OIDC settings ở .env.example. Không lấy bốn database đang dùng làm integration fixtures. Core factory từ chối startup khi thiếu trusted OIDC configuration.

Migrate trên đích thử đã được phép, không dùng reset command của milestone database:

```powershell
$env:DXOS_CORE_URL = '<isolated PostgreSQL URL from secure local configuration>'
$env:DXOS_LAYOUT = 'databases'
uv run --locked --group database python -m scripts.database upgrade --database core
```

Operator bootstrap principal IT từ issuer/subject đã xác minh và departments cần dùng; không seed user/admin tự động từ manifest, tên group hoặc payload. Chỉ thao tác trên database/realm thử được cho phép.

```sh
uv run --locked --group database --env-file .env.test uvicorn app.core.runtime:create_app --factory --host 127.0.0.1 --port 8000
```

Worker trong terminal riêng, nạp cùng file cấu hình test:

```sh
uv run --locked --group database --env-file .env.test python -m app.core.runtime worker
```

Composition mặc định đọc .env và process environment; uv --env-file nạp file test trước factory/worker. File test phải khai báo rõ FLOWBRIDGE_CORE_DATABASE_URL để không dùng nhầm Core URL trong .env thật. Resource HTTP clients/engines đóng khi HTTP lifespan hoặc worker kết thúc. Không bắt đầu worker bằng BackgroundTasks trong API.

### PostgreSQL integration trên Windows

Máy kiểm chứng có PostgreSQL 17 và OpenSSL từ Git. Script sau tạo cluster TLS **local loopback riêng** trong .tmp, chạy tests rồi dừng cluster; không dùng Supabase hoặc service PostgreSQL đang có. Có thể truyền -PostgresBin, -OpenSsl, -Port theo cài đặt thực tế:

```powershell
.\scripts\test_postgres.ps1
```

Script từ chối port đang dùng, không cài service hoặc trust certificate vào Windows. Cluster dùng local trust authentication và dữ liệu tổng hợp; không dùng profile đó làm cấu hình production. Files/log/cert còn trong .tmp bị ignore để chẩn đoán. Fixtures tạo 3 database tên flowbridge_it_<random>_{core,stock,consumer}, chỉ cleanup những tên chúng vừa tạo; không fallback về DXOS_ADMIN_URL.

Với server thử được cấp riêng và DXOS_TEST_ADMIN_URL đã export:

```sh
uv run --locked --group database pytest tests/core/persistence -m integration -q
```

Test không có test admin URL sẽ SKIP, không phải PASS. Không chỉ định server production hoặc Supabase chung để tạo/xóa database nếu chưa được cho phép rõ ràng.

Hồi quy migrations/seed của gói database bằng cùng cluster runner:

```powershell
.\scripts\test_postgres.ps1 -TestPath tests/e2e/database
```

Nhóm này có marker e2e theo lịch sử bàn giao nhưng bằng chứng chỉ là PostgreSQL/migrations xuyên database, **không phải** chuỗi Core–Keycloak–Hasura.

## Dịch vụ ngoài chưa cấu hình

Môi trường kiểm chứng chưa có endpoint/credentials Keycloak và Hasura được cấu hình. PostgreSQL integration dùng **test-only verified-identity dependency override**, không xác nhận SSO hoặc gọi mock Hasura rồi báo E2E.

### Keycloak

Cần một instance chạy riêng có HTTPS, realm/client Core với introspection credentials và audience/client allowlists phù hợp. Thiết lập FLOWBRIDGE_OIDC_ISSUER, _AUDIENCE, _INTROSPECTION_URL, _CLIENT_ID, _CLIENT_SECRET, _ALLOWED_CLIENTS và CA_BUNDLE nếu dùng CA thử. Không tắt TLS verification hoặc tự sửa realm thật.

Chưa tìm thấy executable Keycloak được cấu hình nên không có lệnh startup đã chạy/xác minh. Với bản phân phối native đã cài, tham khảo [Keycloak TLS](https://www.keycloak.org/server/enabletls) cho kc.bat/kc.sh start với certificate/key; hostname, port và database engine phải do operator chốt. Không tự provision tài khoản để vượt blocker.

### Hasura v2 CE

Cần Metadata API riêng với admin secret, metadata database **riêng do engine quản lý**, và từ Hasura truy cập được các database thử. Cài service native độc lập trên hệ điều hành được hỗ trợ, không yêu cầu Docker. Chưa có executable/endpoint được xác minh trên máy này nên không đưa một lệnh cài/chạy suy đoán.

Cấu hình FLOWBRIDGE_HASURA_ENDPOINT, _ADMIN_SECRET, _ALLOWED_DATABASE_HOSTS, _ALLOWED_SOURCES và _SECRET_BINDINGS. Mỗi secret ref trỏ env var DSN PostgreSQL reader ít quyền, có TLS; không dùng DXOS_ADMIN_URL làm source. FLOWBRIDGE_CORE_SOURCE_SCOPES giới hạn các object được discovery, ví dụ {"inventory-reader":[{"schema":"public","name":"stock_available"}]}. Manifest không chứa password.

Internal read profile cần HASURA_GRAPHQL_JWT_SECRET dạng HS256 với key riêng tối thiểu 32 bytes, issuer/audience khớp FLOWBRIDGE_CORE_HASURA_JWT_*; xem [Hasura JWT](https://hasura.io/docs/2.0/auth/authentication/jwt/). Không dùng admin secret làm signing key hay read token. Provider chỉ ký role của department từ TrustedHasuraContext, TTL tối đa 10 giây; không trả credential này ra browser.

Chỉ bật FLOWBRIDGE_CORE_VERIFIED_HASURA_AUTH_PROFILE=true sau khi kiểm thực tế JWT/profile, allow/deny, namespace mapping và ingress riêng chặn truy cập bỏ qua Core. Token ngắn hạn không thay kiểm grant hiện hành và không biến GraphQL công khai thành an toàn. Metadata API luôn private. Các biến engine chính thức, gồm metadata DB/JWT, xem [Hasura configuration](https://hasura.io/docs/2.0/deployment/graphql-engine-flags/reference/).

## E2E opt-in với dịch vụ thật

Harness tại tests/e2e/test_automated_onboarding.py gọi Core HTTP, introspection qua Core, worker chạy riêng, Hasura Metadata/GraphQL thật. Không override identity/repository/engine. Harness hiện **chưa chạy tới upstream**, chỉ xác nhận SKIP khi không opt-in; không coi là bằng chứng E2E PASS.

Operator chuẩn bị trước, chỉ trên môi trường thử riêng:

1. Ba database khác nhau, tên bắt đầu flowbridge_e2e_: Core đã migrate, nguồn owner và nguồn consumer. Không dùng schema thử trong database đang dùng. Hasura metadata DB/instance và Keycloak realm/client cũng phải dành cho test.
2. Nguồn owner có public.stock_available(sku text PRIMARY KEY, available_quantity integer NOT NULL, unit_cost numeric NOT NULL), đúng một dòng tổng hợp sku=SYNTHETIC-SKU. Nguồn consumer có public.order_refs(order_id text PRIMARY KEY). Cấp reader riêng cho Core/Hasura và writer riêng cho kiểm thử cập nhật; writer không dùng làm secret_ref.
3. Ba danh tính thật đã được xác minh issuer/subject: IT có PLATFORM_ADMIN; consumer và outsider có NONE. Bootstrap principals/departments/memberships trong Core thử bằng operator tin cậy. Consumer thuộc department của submission consumer; outsider không thuộc department đó. Không nhận group/role tự khai từ manifest. Harness đối chiếu /me của cả ba với Core DB trước khi gửi lệnh mutation.
4. Hai file OnboardingSubmission JSON đúng contract hiện hành, mỗi file một source/database riêng, key ứng dụng mới chưa tồn tại. Có thể tham khảo submission builder trong tests/core/persistence/test_onboarding.py và contracts/application-onboarding.yaml. Owner khai báo bảng stock_available, chỉ hai fields sku và available_quantity, alias cùng tên, TEXT/string và INTEGER/integer, business_key=[sku], không relationships. Consumer khai báo order_refs/order_id. Đặt port hoặc dùng mặc định 5432. App/source keys, secret refs, host scopes và Hasura allowed_sources phải khớp cấu hình runtime. Không thay code Core.
5. API/worker dùng cùng .env.test, Core URL trỏ Core thử; secrets/from_env ở Hasura cũng trỏ đúng nguồn thử. Profile JWT/private ingress được kiểm trước khi bật verified flag. Cấp token IT/consumer/outsider đủ sống suốt bài test.

Đặt các biến sau trong .env.e2e cục bộ (đã bị ignore), không commit token/DSN:

| Biến với tiền tố FLOWBRIDGE_E2E_ | Giá trị |
|---|---|
| ENABLED | 1, chỉ khi chấp thuận chạy mutation trên môi trường thử |
| CORE_URL | Origin API thật, ví dụ http://127.0.0.1:8000 |
| CORE_DATABASE_URL | postgresql+psycopg URL Core thử, dùng để kiểm binding và isolation |
| OWNER_WRITE_URL | postgresql+psycopg URL writer của nguồn owner thử |
| OWNER_SUBMISSION / CONSUMER_SUBMISSION | Đường dẫn hai file submission JSON cục bộ, nên đặt trong .tmp |
| IT_TOKEN / CONSUMER_TOKEN / OUTSIDER_TOKEN | Access tokens thật từ IdP thử, không có tiền tố Bearer |
| HASURA_ENDPOINT / HASURA_ADMIN_SECRET | Origin/secret Metadata API của instance thử |

CA thử phải được tin cậy qua cấu hình CA hợp lệ; không dùng verify=false. E2E không tự tạo user/realm, không migrate/reset/drop database. Trước mutation, harness chặn database không có prefix thử, kiểm ba database độc lập, source writer tuple, Core /me bindings và dòng tổng hợp. Test import hai ứng dụng, explicit publish/approve, kiểm SELECT metadata chỉ hai cột, allow/deny, cập nhật nguồn, revoke chặn ngay và permission được gỡ sau job. HTTP retry cùng key được replay; restart process được kiểm riêng ở PostgreSQL integration, không giả lập restart IdP/Hasura.

```sh
uv run --locked --group database --env-file .env.e2e pytest tests/e2e/test_automated_onboarding.py -m e2e -q -rs
```

Harness trả lại quantity gốc, cố enqueue revoke nếu bài test lỗi sau approval; không xóa registry/audit hoặc nguồn đã track. Nếu cleanup không hoàn tất, operator xử lý grant/job trên môi trường thử. Mỗi lần chạy lại dùng bộ test mới hoặc app/source keys mới được allowlist. Không dùng -s/--showlocals khi chạy với credentials thật; giữ log Metadata API riêng tư.

## Ma trận nghiệm thu

| ID | Trạng thái hiện tại | Bằng chứng / giới hạn |
|---|---|---|
| T01 Manifest registration | PostgreSQL integration | HTTP → discovery thật → atomic draft import; không công bố |
| T02 Invalid Manifest | PostgreSQL integration | HTTP 422, không tạo registry |
| T03 Schema Discovery | PostgreSQL integration | SQLAlchemy Inspector đọc table, columns, PK thật |
| T04 Compilation | PostgreSQL integration | Chỉ sku/available_quantity; unit_cost không vào contract |
| T05 Publication | BLOCKED | Chưa có Hasura Metadata API/auth profile thật |
| T06 Authorized Query | BLOCKED | Cần Hasura + Keycloak/consumer binding thật |
| T07 Unauthorized Query | BLOCKED cho E2E | Offline policy và PostgreSQL OWNER rejection không thay SSO/Hasura enforcement |
| T08 Source Update | BLOCKED cho E2E | Chưa đọc qua Data API thật |
| T09 Revoke | BLOCKED cho E2E | Chưa nghiệm thu engine permission removal/allow-deny |
| T10 Retry/Restart | PostgreSQL integration | API replay từ Python process mới; rollback; concurrent worker claims/reclaim/fencing |

Kiểm chứng ngày 23/10/2026: **11 Core PostgreSQL integration tests passed** (19.32 giây), **37 database migration/seed integration tests passed** (34.41 giây), trên cluster TLS PostgreSQL 17.11 local được tạo/dừng riêng mỗi lần. Grant persistence test dùng fixture trạng thái engine để kiểm SQL commands/request/grant/revoke jobs; không xem fixture đó là publication đã được Hasura thực hiện. E2E opt-in hiện SKIP/BLOCKED do chưa cấu hình dịch vụ thật. Các cluster được dừng sau test; database nhóm không bị sửa.

Toàn bộ suite offline: **730 passed, 50 integration/E2E deselected** (32.52 giây). Ruff lint/format, git diff --check và build sdist/wheel offline đạt. Kiểm package members không có .env thật, .tmp, cache, archive, backups, PDF tham chiếu hoặc pgAdmin local config. Secret scan source phát hành không có password từ cấu hình cục bộ. Các kết quả này không chứng minh deployment auth/engine đã hoạt động.

Không dùng khái niệm E2E PASS cho các integration tests trên. Sau khi có dịch vụ thử: chạy harness và nghiệm thu T01–T10 qua Core HTTP thật với token IT/consumer thật. Workflow/Agent, UI và Docker không thuộc milestone này.
