# Chạy và kiểm thử SSO Keycloak với Supabase

Backend chỉ dùng danh tính đã được IdP xác minh; không có sample identity, tài khoản mặc định qua HTTP hoặc header giả danh. Keycloak xử lý đăng nhập/phiên; user/grant và quyền tài nguyên nằm trong Supabase.

Hai API Identity core, binding tenant/application, scope và profile OTP bắt buộc mới
được hướng dẫn riêng tại [Identity core](IDENTITY-CORE.md). Profile SSO cũ không tự
được cập nhật hoặc bật core; tạo profile mới khi kiểm chính sách IAM mới.

## 1. Chuẩn bị môi trường

Chạy tại root FlowBridge, dùng môi trường Python trong README. Native local cần OpenJDK 25, OpenSSL, Node >=20 và npm. Metadata phiên bản/URL/checksum nằm ở [dependencies.json](../infra/identity/dependencies.json): Keycloak 26.8.1, Temurin 25.0.4.1+1 cho Windows, SDK 26.2.4 và Playwright 1.64.1. Tải qua nguồn upstream, kiểm SHA-256 rồi giải nén vào `.tools/sso/`; giữ nguyên LICENSE/NOTICE. Không cần cài Docker hoặc thay Java hệ thống cho cách chạy này.

Giữ cấu hình Supabase trong `.env` đang có. Database cần migration khớp Alembic head của source và role/catalog đã thiết lập; SSO không tự migrate hoặc seed. Kiểm tra bằng `scripts/check_db.py` và `alembic current` nếu cần. Không chạy lại seed trên DB đã có dữ liệu.

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

Frontend React khi phát triển tại `http://127.0.0.1:5174/` dùng cùng client A,
callback và web origin 5174 được đăng ký chính xác trong template local. Vite proxy
API cùng origin; không thêm wildcard hoặc mở CORS backend. Profile cũ cập nhật bằng
`scripts/configure_web_dev.py` theo [hướng dẫn web](../web/README.md).

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

Suite SQL nghiệp vụ và fixture SSO yêu cầu revision DB khớp chính xác Alembic heads. Không stamp/reset để vượt guard. E2E stack đầy đủ theo [hướng dẫn chạy](../HUONG-DAN-CHAY.md#kiểm-thử-hệ-thống).

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


## Thiết kế xác thực

Backend vẫn headless. Keycloak sở hữu đăng nhập, password, PKCE và phiên; API xác minh access token qua RFC 7662 bằng confidential client. Tái sử dụng HTTPX client theo lifespan, timeout và kiểm issuer/audience/subject/expiry/nbf/token_type/client. Không cache kết quả cho phép truy cập. Service client registry phía server quyết định loại account, rồi policy đối chiếu với users.account_type trong PostgreSQL. Role/grant/resource do backend kiểm lại cho mỗi request.

Dùng introspection để xác minh token tại IAM. Không tự triển khai JWT/JWKS verifier hoặc coi rotation test là đã chạy. IdP không sẵn sàng trả 503/504; thiếu/sai token trả 401. Registry client có thể giới hạn client phát token được chấp nhận. CORS dùng allowlist, không thay xác thực.

Directory adapter dùng Admin REST API chỉ đọc, client có `view-users` và `view-clients` trong realm ứng dụng; tách credential khỏi introspection. `UserProvision.identity_reference` là UUID user đã tồn tại ở Keycloak. API đọc metadata thật để liên kết issuer+subject; không tạo user/password ở IdP, không tự cấp grant hoặc reactivate hồ sơ. Vì không ghi IdP, transaction/idempotency chỉ cần bảo đảm phần DB, tránh distributed-write saga giả. Service account được đối chiếu qua endpoint service-account-user của client; không suy ra loại tài khoản từ username. Tạo tài khoản tại IdP dùng giao diện/công cụ upstream; provisioning hai chiều hoặc đăng ký tự động là scope sau.

Bootstrap CLI là thao tác operator có apply rõ ràng. Mặc định kiểm tra bằng transaction rồi rollback, kể cả outbox. ADMIN đầu tiên chỉ tạo khi chưa có ADMIN active, hoặc retry đúng ADMIN duy nhất đó; các lần link/grant khác cần UUID ADMIN active. Kết quả grant không đổi loại account/phòng ban/profile đang có. PostgreSQL advisory lock và row lock bảo vệ retry/concurrency; không có migration mới.

Hai browser client dùng `keycloak-js` qua npm, không copy source SDK. Test E2E gọi TCP listener và Keycloak + Supabase thật, không override authenticated_identity. Local certificate do OpenSSL tạo; HTTP client dùng CA cụ thể. Browser test pin SPKI của đúng certificate local trong browser tạm, không đặt ignoreHTTPSErrors hoặc bỏ TLS verification toàn cục. Trace/video/screenshot tắt để không lưu token/password.

Realm template khai báo SubMapper chuẩn cho access token/introspection; custom scope không dựa vào basic scope tự thêm. Native local profile dùng Keycloak dev/H2 độc lập, không tạo bảng IAM trong Supabase. Nó chỉ bind loopback; dev mode có listener HTTP bổ sung theo upstream nên không dùng profile này cho production. Production cần database IAM/persistence/TLS/secret management và network policy theo tài liệu upstream. Các image phải rà riêng trước phát hành; nghiệm thu native không chứng nhận image.

Dependency đã pin tại infra/identity/dependencies.json và clients/sso/package-lock.json: Keycloak 26.8.1, SDK 26.2.4, Playwright 1.64.1 (Apache-2.0), Temurin 25 native test (GPL với Classpath exception). File tải về giữ license/notice trong cache/tool directory bị ignore, không đóng gói vào source/wheel. Không thêm dependency Python.

Nguồn: [Keycloak OIDC](https://www.keycloak.org/securing-apps/oidc-layers), [JavaScript adapter](https://www.keycloak.org/securing-apps/javascript-adapter), [Admin REST](https://www.keycloak.org/docs-api/latest/rest-api/index.html), [cấu hình server](https://www.keycloak.org/server/configuration).
