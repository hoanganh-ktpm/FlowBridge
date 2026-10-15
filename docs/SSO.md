# Chạy và kiểm thử SSO Keycloak với Supabase

Backend chỉ dùng danh tính đã được IdP xác minh; không có sample identity, tài khoản mặc định qua HTTP hoặc header giả danh. Keycloak xử lý đăng nhập/phiên; user/grant và quyền tài nguyên nằm trong Supabase. Chi tiết thiết kế: [ADR 0005](SSO-ADR.md). Kết quả đã chạy và giới hạn: [báo cáo kiểm thử](SSO-TEST-RESULTS.md).

## 1. Chuẩn bị môi trường

Chạy tại root FlowBridge, dùng môi trường Python trong README. Native local cần OpenJDK 25, OpenSSL, Node >=20 và npm. Metadata phiên bản/URL/checksum nằm ở [dependencies.json](../infra/identity/dependencies.json): Keycloak 26.8.1, Temurin 25.0.4.1+1 cho Windows, SDK 26.2.4 và Playwright 1.64.1. Tải qua nguồn upstream, kiểm SHA-256 rồi giải nén vào `.tools/sso/`; giữ nguyên LICENSE/NOTICE. Không cần cài Docker hoặc thay Java hệ thống cho cách chạy này.

Giữ cấu hình Supabase trong `.env` đang có. Database cần migration `0002_api_idempotency` và role/catalog đã thiết lập; SSO không tự migrate hoặc seed. Kiểm tra bằng `scripts/check_db.py` và `alembic current` nếu cần. Không chạy lại seed trên DB đã có dữ liệu.

```powershell
& ./.venv/Scripts/python.exe scripts/prepare_identity.py --test-users
```

Lệnh tạo CA/certificate, realm import, secret và 3 user local thật ở `.cache/identity/`. Nó không khởi động server, không ghi Supabase hoặc đổi `.env`. Tài khoản được Keycloak tạo khi import, đăng nhập thật bằng PKCE; không phải actor giả trong API. Không gửi `operator.json`, `runtime.json`, private key hoặc mật khẩu vào Git/chat/log. Generator từ chối ghi đè profile hiện có; giữ state và IAM DB cùng nhau khi chạy lại.

## 2. Khởi động IAM, API và client

Terminal A — Keycloak native, dùng Java riêng:

```powershell
& ./infra/identity/start-keycloak.ps1 -KeycloakHome .tools/sso/keycloak-26.8.1 -JavaHome '.tools/sso/jdk-25.0.4.1+1'
```

IAM lưu H2 riêng ở IdentityDir, tách database nghiệp vụ. Profile này là local dev/H2, bind loopback và dùng issuer `https://localhost:18443/realms/flowbridge`; không dùng cho production. Chứng chỉ chỉ dùng local và không được launcher cài vào trust store hệ thống. Browser của người dùng cần tin CA local bằng cơ chế môi trường được quản lý; backend đã đọc CA cụ thể. Hướng triển khai production theo [Keycloak TLS](https://www.keycloak.org/server/enabletls), persistence và network policy riêng.

Terminal B — API đọc DB từ `.env`, SSO từ profile riêng:

```powershell
& ./.venv/Scripts/python.exe scripts/run_sso_api.py
```

Khi chạy API Supabase + SSO, giữ `FLOWBRIDGE_RAG_DEV_ENABLED=false`: RAG dev từ upstream là profile loopback riêng yêu cầu DSN trống. ML optional cần artifact/context theo [ML-SERVICE.md](ML-SERVICE.md); test SSO không cần bật ML.

Mở [Swagger](http://127.0.0.1:8000/docs); `/_ops/ready` chỉ xác nhận runtime/database, không chứng nhận SSO. Cấu hình thật có thể đưa vào `.env` theo `.env.example` rồi chạy entrypoint `python -m flowbridge.main` như trước; helper trên chỉ kết hợp profile để không ghi đè cấu hình cá nhân.

Terminal C/D — hai client ở tầng ứng dụng:

```powershell
Set-Location clients/sso
npm ci --ignore-scripts
npm run start:a
# Terminal khác, cùng thư mục:
npm run start:b
```

Client A: `http://127.0.0.1:8761`, B: `http://127.0.0.1:8762`. SDK lấy cấu hình public từ `/config.json`; không phục vụ thư mục state hoặc secret. Nút đăng nhập chuyển đến Keycloak. Token giữ trong bộ nhớ theo SDK; nút sao chép access token phục vụ nhập vào Authorize của Swagger. Mỗi client có clientId riêng, cùng realm, code+PKCE S256 và redirect allowlist.

## 3. Liên kết user và cấp quyền có chủ đích

Đăng nhập Keycloak Admin Console bằng tài khoản operator local trong file state để quản lý user và lấy UUID. Các tài khoản `flowbridge-admin`, `flowbridge-employee`, `flowbridge-other` là user IdP test, chưa tự có grant ứng dụng. Không dùng email hoặc username làm khóa đăng nhập trong DB; khóa là issuer+subject.

Bootstrap ADMIN đầu tiên, xem trước rồi mới apply:

```powershell
& ./.venv/Scripts/python.exe scripts/bootstrap_identity.py --identity-dir .cache/identity --subject YOUR_KEYCLOAK_USER_UUID --bootstrap-admin
& ./.venv/Scripts/python.exe scripts/bootstrap_identity.py --identity-dir .cache/identity --subject YOUR_KEYCLOAK_USER_UUID --bootstrap-admin --apply
```

Nếu DB đã có ADMIN active, cờ bootstrap không tạo thêm ADMIN tùy ý. Chỉ retry cùng ADMIN duy nhất đã bootstrap mới được chấp nhận; các lần link/grant sau dùng UUID ADMIN ứng dụng đã được chỉ định qua `--granted-by`. Xác minh actor/target trước khi apply trên môi trường dùng chung. CLI mặc định thực thi kiểm tra trong transaction rồi rollback toàn bộ; `applied=false` không chứng nhận đã ghi user/grant.

Liên kết employee và cấp role, với phòng ban active có sẵn:

```powershell
& ./.venv/Scripts/python.exe scripts/bootstrap_identity.py --identity-dir .cache/identity --subject YOUR_EMPLOYEE_IDP_UUID --grant EMPLOYEE --department YOUR_DEPARTMENT_UUID --granted-by YOUR_APP_ADMIN_UUID
# Kiểm kết quả, thêm --apply khi muốn commit.
```

Client directory chỉ có quyền đọc `view-users` và `view-clients` của realm ứng dụng; tách secret khỏi introspection. Adapter kiểm user active và service-account binding bằng Admin API. CLI/API không tạo hoặc sửa password/identity ở IdP, không tự reactivate hoặc đổi profile/grant khi link lại. Các API quản trị role hiện có dùng ADMIN đã xác minh.

`POST /admin/v1/users` nhận `identity_reference` là UUID user đã có tại Keycloak và `account_type` khớp metadata thật. Nó liên kết user vào DB, không tự cấp role. Request cần bearer ADMIN và Idempotency-Key. User creation/provisioning hai chiều và MFA rollout là task riêng nếu cần chính sách sản phẩm; không giả nhận hoàn thành từ việc link thành công.

Service accounts dùng client `flowbridge-ai`/`flowbridge-po`, client credentials và grant AI_SERVICE/PO_SERVICE, account_type service. Registry phía backend phải gồm đúng các service clients đó; service token không dùng thay human hoặc nâng quyền bằng X-Role/X-User-Id.

## 4. Test tự động

Unit offline:

```powershell
& ./.venv/Scripts/python.exe -m pytest tests/unit -q
```

SQL integration (tiêm danh tính trong bộ regression này; không thay nghiệm thu SSO):

```powershell
& ./.venv/Scripts/python.exe -m pytest tests/integration/test_supplier_database.py -m integration -q
```

E2E thật cần IAM/API đang chạy, profile đã sinh test users và npm ci đã hoàn tất. Nó xác minh user qua IdP, tạo riêng department/user/grant test UUID trong Supabase rồi cleanup; từ chối dùng lại user có sẵn để tránh xóa nhầm dữ liệu. Dùng profile test riêng thay vì tài khoản đã bootstrap thủ công. API URL/port trong `public.json` phải khớp listener.

```powershell
# Ví dụ profile mặc định, API port 8000; chuẩn bị trước khi chạy Playwright.
& ./.venv/Scripts/python.exe tests/e2e/identity_fixture.py --identity-dir .cache/identity
$env:FLOWBRIDGE_SSO_IDENTITY_DIR = (Resolve-Path .cache/identity).Path
$env:NODE_EXTRA_CA_CERTS = Join-Path $env:FLOWBRIDGE_SSO_IDENTITY_DIR 'certs/ca.pem'
# Nếu dùng Edge đã cài; hoặc bỏ biến này và dùng npx playwright install chromium.
$env:FLOWBRIDGE_BROWSER_CHANNEL = 'msedge'
Set-Location clients/sso
npm run test:e2e
Set-Location ../..
& ./.venv/Scripts/python.exe tests/e2e/identity_fixture.py --identity-dir .cache/identity --cleanup
```

Playwright tự phục vụ client A/B; dừng các client server thủ công trước khi chạy E2E. Browser test tạm chỉ tin SPKI của certificate local trong profile; API/Node dùng CA thật, không có ignoreHTTPSErrors hoặc verify=False. Trace/video/screenshot tắt để không lưu token. Nếu lệnh bị gián đoạn, kiểm state/session và chạy cleanup theo `fixture.json` trước retry. Không xóa fixture state trước khi dọn DB. Không dùng profile này với IAM hay tài khoản production.

Suite nghiệm thu gửi TCP HTTP thật và không override authentication: login PKCE, token/ID token/refresh token sai mục đích, user/grant/resource, persistence/idempotency, SSO A→B, refresh/logout, ADMIN link và service roles. Case lỗi IdP/expiry và malformed claims có unit riêng; MFA và production image chưa được nghiệm thu. Thiếu config/engine phải fail, không skip để báo pass.

## 5. Khi gặp lỗi

| Kết quả | Kiểm tra |
|---|---|
| `/docs` 404 | Bật docs và restart server; helper SSO bật docs trong profile |
| API 401 dù đăng nhập thành công | Issuer, audience, sub, access-token type và client registry; realm template có subject mapper chuẩn cho custom scope |
| API 403 | Exact issuer+subject, user active, account_type, grant/scope; IAM login không tự cấp quyền ứng dụng |
| API 503/504 khi xác thực/directory | Secret, CA trust, URL, quyền directory, timeout/kết nối IdP |
| Browser báo TLS/CORS | Tin CA local đúng cách, callback/origin khớp; không tắt kiểm TLS hoặc đổi allowlist thành wildcard |
| State đã tồn tại | Dùng lại profile và IAM DB của nó; import không ghi đè realm đã có |

Không có migration mới cho SSO này. Các endpoint workflow/chat/model/document vẫn cần engine riêng; đăng nhập thành công không thay các dependency đó.
