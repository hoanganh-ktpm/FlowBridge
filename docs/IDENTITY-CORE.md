# Identity core

Dùng [SSO ứng dụng](SSO.md) và contract app v0.4.1.

## Cấu hình

Chỉ bật khi database/issuer/tenant/application thuộc deployment đã xác định. Thêm JSON qua Settings/env, không sửa source. Ví dụ thay các ID minh họa bằng binding thực:

```dotenv
FLOWBRIDGE_CORE_IDENTITY='{"tenant_id":"YOUR_TENANT","application_id":"YOUR_APP","web_client_id":"flowbridge-web-a","mobile_client_id":"flowbridge-web-b","role_permissions":{"EMPLOYEE":["identity:read"],"ADMIN":["identity:read"],"AI_SERVICE":["identity:read"]},"service_clients":{"flowbridge-ai":{"role":"AI_SERVICE","permissions":["identity:read"]}}}'
```

Các client phải nằm trong `FLOWBRIDGE_OIDC_ALLOWED_CLIENTS`; service cũng phải nằm trong `FLOWBRIDGE_OIDC_SERVICE_CLIENTS`. Cần database, issuer, audience, introspection URL/client/secret đầy đủ. Service principal còn cần directory read-only và credential như SSO.md. Issuer phải là Keycloak HTTPS và introspection URL đúng `/protocol/openid-connect/token/introspect` của issuer đó. CA dùng cấu hình riêng, không tắt kiểm TLS.

`role_permissions` là allowlist tường minh; scope token không tự cấp quyền khi DB không có grant tương ứng. Không đặt quyền Data/Workflow trước khi receiver policy và resource binding đã được triển khai. Profile sinh tự động chỉ cấp `identity:read`, không bật delegation hoặc job access cho engine chưa tích hợp.

## API

| Endpoint | Xác thực | Kết quả |
|---|---|---|
| `GET /api/v1/core/identity/config` | Công khai | IdentityConfig: issuer, auth/token/JWKS endpoints, public web/mobile client IDs, scopes, PKCE=true; không secret |
| `GET /api/v1/core/identity/me` | Human access token, scope identity:read, active DB grant | Principal: id, tenant_id, display_name, roles, permissions |

Thiếu/sai/hết hạn bearer: 401. Service token gọi me, thiếu scope/quyền hoặc user disabled: 403. Dependency lỗi/timeout: 503/504, không trả exception/credential. Tenant/role/user headers không thay principal. Correlation và error envelope dùng boundary chung.

Khi bật binding, runtime DB có **48 operation**, hoặc **53 khi bật ML**. Hai operation core có `operationId` đúng contract 0.2.0; 26 core operation khác chưa mount. OpenAPI namespace `Core*` tránh đè schema app/ML. Không cấu hình binding thì runtime giữ 46/51 operation. RAG development vẫn là profile riêng không DB.

Xuất mapping thêm hai API để chuẩn bị Gateway:

```sh
uv run --locked python -m flowbridge.gateway --include-identity --upstream http://127.0.0.1:8000
```

Mapping không tự triển khai gateway hoặc chứng minh APISIX chạy.

## Test với IAM và DB thật

Native toolchain/CA theo SSO.md; dùng profile mới và port trống. Lệnh dưới tạo fixture test riêng, không phải cấu hình tenant production:

```powershell
& ./.venv/Scripts/python.exe scripts/prepare_identity.py --out .cache/identity-core-test --port 19444 --api-url http://127.0.0.1:18082 --test-users --core-tenant core-test-tenant --core-application supplier-assistant --test-token-lifespan 20
# Terminal IAM
& ./infra/identity/start-keycloak.ps1 -KeycloakHome .tools/sso/keycloak-26.8.1 -JavaHome '.tools/sso/jdk-25.0.4.1+1' -IdentityDir .cache/identity-core-test
# Terminal API
& ./.venv/Scripts/python.exe scripts/run_sso_api.py --identity-dir .cache/identity-core-test --port 18082
# Sau khi IAM/API sẵn sàng
& ./.venv/Scripts/python.exe tests/e2e/identity_fixture.py --identity-dir .cache/identity-core-test
$env:FLOWBRIDGE_SSO_IDENTITY_DIR = (Resolve-Path .cache/identity-core-test).Path
$env:NODE_EXTRA_CA_CERTS = Join-Path $env:FLOWBRIDGE_SSO_IDENTITY_DIR 'certs/ca.pem'
$env:FLOWBRIDGE_BROWSER_CHANNEL = 'msedge'
Set-Location clients/sso
npm run test:e2e
Set-Location ../..
& ./.venv/Scripts/python.exe -m pytest tests/integration/test_identity_core_database.py -m integration -q
# Luôn cleanup đúng profile sau test, kể cả fail; đối soát nếu bị gián đoạn.
& ./.venv/Scripts/python.exe tests/e2e/identity_fixture.py --identity-dir .cache/identity-core-test --cleanup
```

Profile core chạy cả suite SSO và core; profile SSO cũ chỉ chạy sso.spec.mjs. Chọn riêng core.spec.mjs với profile thiếu binding phải báo không có test/lỗi, không được coi đạt core. Không thêm skip hoặc success fallback khi thiếu IAM/DB/config.

Test helper đăng ký OTP với Keycloak thật và giữ secret test trong `test-otp.json` của profile bị ignore; chỉ profile local/test. Trace/video/screenshot và snapshot trang lỗi tắt để tránh ghi token/OTP. Không dùng helper với tài khoản production. Token 20 giây chỉ phục vụ test expiry và yêu cầu `--test-users`; mặc định vẫn 180 giây. Không dùng profile có fixture còn dở hoặc ghi đè profile cũ.

Unit policy/contract: `uv run --locked pytest tests/unit/test_identity_core.py -q`. Các test context dùng registry/mock ở unit, không thay bằng chứng JobRegistry production.

## Boundary accounts/bootstrap

`IdentityAccounts` và `bootstrap_identity` dùng `IdentityAccountRepository`; SQL giữ
nguyên bảng users/roles/user_roles/departments và khóa transaction hiện có. Ứng dụng
truyền adapter với session request; CLI bootstrap dùng adapter trực tiếp. Identity
không import repository, policy hoặc service của Supplier Assistant.

`IdentityOutbox` triển khai `IdentityAuditSink`, ghi `identity.bootstrap_applied`
trong cùng session/transaction với user và grant. Caller quyết định commit/rollback;
retry không thay profile/grant hiện có hoặc thêm event. Đây là event outbox pending,
chưa phải broker delivery hoặc audit bất biến. Role/scope vẫn theo schema deployment
hiện có; thay boundary không tự cung cấp registry đa ứng dụng.

```sh
uv run --locked pytest tests/integration/test_identity_accounts_database.py -m integration -q
```

Suite này cần PostgreSQL được cấu hình, tạo fixture UUID riêng và rollback toàn bộ;
kiểm các cột Identity đang dùng và xác minh cleanup qua kết nối mới. Không áp dụng
migration. Nó kiểm SQL account/bootstrap, không thay E2E IAM/browser hoặc regression
toàn database.


## Thiết kế và ranh giới quyền


Deployment hiện có một database nghiệp vụ và một issuer Keycloak. Chọn **single tenant, một application binding mỗi deployment** qua `Settings.core_identity` (`FLOWBRIDGE_CORE_IDENTITY` dạng JSON). Operator xác định tenant/application và gắn database, issuer, audience, public clients, service clients tương ứng. Không suy tenant từ username/email, header, prompt hoặc payload. Đây không phải cơ chế cách ly nhiều tenant trong cùng database; muốn mở rộng cần registry/migration và test cách ly riêng.

Không có tenant mặc định. Khi binding chưa được cấu hình, hai route core không mount và 46 operation app/AI giữ nguyên. Khi cấu hình thiếu/mâu thuẫn, Settings từ chối startup. Backend dùng Keycloak endpoints của đúng issuer đã cấu hình; tiếp tục RFC 7662, không tự viết JWT verifier hoặc phát token mới.

Adapter `identity/repository.py` đọc users/roles/user_roles/departments bằng metadata chung và một SQL statement mỗi lần resolve. Không import repository/policy Supplier Assistant. Chỉ identity active, role active, grant chưa thu hồi, đúng account type/scope và department active mới được nhận. Không cache quyền; tắt user/role hoặc thu hồi grant tác động lần resolve tiếp theo.

Principal public giữ năm trường contract core 0.2.0. `id` là ID registry nội bộ, không đánh đồng với IdP subject. Roles được định danh `application:ROLE`, hoặc `application:ROLE@department`. Permissions là giao của allowlist `role_permissions` và OAuth scopes do IAM đã xác minh. Grant phòng ban chỉ cho `identity:read` ở lớp này; quyền dữ liệu/phê duyệt theo tài nguyên phải có receiver policy, không được tự nâng thành quyền toàn tenant. ADMIN không có wildcard hoặc quyền duyệt mặc định.

Service phải nằm trong cả OIDC registry và core binding, có scope token cần thiết, có global role DB khớp binding; directory còn xác minh subject thuộc đúng service-account client. `/identity/me` chỉ nhận human, không fallback service thành user khi thiếu scope.

### Delegation và job

`X-User-Context` dùng **user access token do chính IAM tin cậy phát hành**, được introspect với issuer/audience/expiry/client/type như bearer. Không phát JWT riêng hay nhận JSON actor. Service cần cả `context:delegate` và permission yêu cầu; user cần permission đó; kết quả là giao quyền và cùng tenant/application. Duplicate/missing/invalid header, service giả user hoặc kèm JobContext đều bị từ chối. Token này là credential: chỉ chuyển qua kênh nội bộ bảo mật, không log/persist. Chỉ truyền cho receiver thuộc audience đã đăng ký; không dùng user token cho audience khác.

`resolve_job_context` là boundary cho adapter `JobRegistry` tin cậy của ingestion/workers về sau. Đối chiếu job active/expiry, tenant/application/client, dataset, permission và quyền hiện tại của initiator. Kết quả chỉ `data:read:<dataset>`, không cấp workflow permission. **Chưa có registry production**, nên thiếu adapter trả 503; không đọc `analysis_tasks.input_snapshot` hoặc tin job ID thay registry. Không mở endpoint Data/Workflow chưa triển khai chỉ để chứng minh context.

### IAM và MFA

Generator có tùy chọn tạo profile core mới: scope `identity:read`, browser flow dùng username/password và OTP REQUIRED, cookie SSO qua phiên đã xác thực. Không thay realm/profile có sẵn, không bật direct-access password grant, implicit flow hoặc wildcard redirect. Browser B hiện là client PKCE thứ hai dùng kiểm SSO và trường mobile_client_id trong profile test; chưa chứng minh ứng dụng mobile native.

Backend tin chính sách MFA của issuer đã quản lý; introspection không được diễn giải là tự chứng minh số factor. Operator phải import/kiểm flow và các client khi triển khai realm khác. Native H2/TLS local là môi trường test, không chứng nhận persistence/HA/network production.

Không migration/dependency mới. Build dùng toolchain hiện có; source không đóng gói state/token/OTP secret/engine distribution. Public config chỉ xuất endpoints, public client IDs, scopes và PKCE flag.
