# Keycloak SSO và TOTP MFA

Cơ chế đối chiếu với [flow/LoA của Keycloak 26.8.1](https://github.com/keycloak/keycloak/blob/26.8.1/docs/documentation/server_admin/topics/authentication/flows.adoc)
và [Admin REST API 26.8.1](https://www.keycloak.org/docs-api/26.8.1/rest-api/index.html).

## Nguyên nhân và phạm vi

Kiểm tra local ngày 2026-10-25 xác nhận Keycloak **26.8.1**, issuer
`https://localhost:18443/realms/flowbridge-it`, public client `flowbridge-web-a`,
Portal `http://127.0.0.1:5174/`. `config.json` khớp realm/client đang chạy.

- `portal/src/app/session.tsx` đã dùng keycloak-js, Authorization Code + PKCE S256.
  `portal/src/keycloak-theme/login/KcPage.tsx` POST password tới Keycloak; không
  có endpoint Portal tự kiểm mật khẩu. Token chỉ ở memory của SDK; localStorage
  của SDK chỉ giữ state/nonce/PKCE verifier tạm thời cho callback.
- `infra/keycloak/realm.template.json` trước thay đổi chưa cấu hình flow MFA.
  Client live không có browser-flow override, dùng `browser`: Cookie ALTERNATIVE,
  Conditional 2FA theo credential đã có, OTP Form ALTERNATIVE. Người chưa có OTP
  bỏ qua bước hai. Có một SSO session web cũ khi kiểm tra; không thu hồi nó.
- `backend/app/platform/identity/authentication.py` đã introspect qua HTTPS và
  kiểm active/issuer/audience/exp/nbf/type/client/subject, nhưng chưa kiểm `acr`.
  Chữ ký token được Keycloak kiểm tại introspection endpoint đáng tin cậy.
- `scripts/docker_env.py` có public client `flowbridge-e2e` dùng password grant
  cho harness cũ. Guard API mới cũng chặn token thiếu MFA từ client này, tránh
  dùng nó làm đường vòng. Không coi password grant là đăng nhập web hợp lệ.
- `clearToken()` của SDK với `login-required` tự gọi login; trước đây có thể
  chạy đua với logout và che lỗi callback/nonce. SessionProvider nay tắt cờ tự
  redirect khi xóa token, xóa trạng thái UI ngay và giữ logout hint của SDK.

## Chính sách server

Nguồn cấu hình duy nhất: `infra/keycloak/realm.template.json`.

```text
flowbridge-browser-mfa-v2
  Cookie                          ALTERNATIVE
  flowbridge-forms-mfa-v2          ALTERNATIVE
    flowbridge-password-totp-v2    CONDITIONAL
      Condition - LoA 2           REQUIRED, max age 900 seconds
      Username Password Form      REQUIRED
      OTP Form                    REQUIRED
```

Client có `minimum.acr.value=2`, default ACR 2 và mapping `2 → 2`. Minimum ở
server ngăn hạ `acr_values` trong URL. Cookie chỉ khôi phục phiên đã đạt LoA 2
còn hạn; cookie password-only cũ không đủ. Fresh authentication, `prompt=login`
hoặc phiên hết hạn phải qua password + TOTP. Refresh không phải fresh login;
token được refresh vẫn cần `acr="2"` tại API. Không gán claim MFA hardcoded.

OTP là TOTP HmacSHA1, 6 số, chu kỳ 30 giây. OTP Form REQUIRED gọi native
Configure OTP khi chưa có credential: QR và mã xác nhận do Keycloak xử lý.
Credential đã có dùng challenge OTP, không đăng ký QR lại. Không thêm thuật toán
OTP hoặc kho secret vào FlowBridge. Generator trong live test chỉ phục vụ fixture.

Chỉ override client web; không đổi realm browser binding, admin console,
directory service account, recovery flow, user hay credential hiện có. Không
thêm default required action cho toàn realm. LoA reuse có giới hạn 15 phút;
logout chấm dứt phiên và lần đăng nhập sau lại cần OTP.

API mặc định yêu cầu `acr="2"` cho `flowbridge-web-a` và `flowbridge-e2e`.
`FLOWBRIDGE_OIDC_MFA_CLIENTS` phải chứa mọi client web của deployment. Duy trì
`FLOWBRIDGE_OIDC_ALLOWED_CLIENTS` theo allowlist deployment và registry service
clients riêng. Không đánh dấu client người dùng thành service để tránh MFA.
Role Core vẫn resolve theo `(issuer, subject)` và metadata hiện tại; membership
không trở thành PLATFORM_ADMIN. Hasura read JWT/grants và business policy giữ nguyên.

## Áp dụng lên realm đang có

`--import-realm` bỏ qua realm đã tồn tại; sửa file import không cập nhật runtime.
Không import đè, reset DB, đổi volume hay reset password. Công cụ sau chỉ tạo
flow riêng, thêm ACR mapper và cập nhật các trường bảo mật của đúng client.
Nó kiểm phiên bản/policy trước, verify flow trước khi bind, từ chối cấu hình
xung đột, không ghi user/session và mặc định chỉ đọc.

```powershell
# Cấp password admin qua biến môi trường/secret store; không đưa vào command line.
python -m infra.deployment.keycloak_mfa --base-url https://localhost:18443 --realm flowbridge-it --client-id flowbridge-web-a --admin-user flowbridge-test-admin --ca-bundle .tmp/docker-integration/tls/server.crt
# Sau khi kiểm đúng deployment, cùng lệnh với --apply để áp dụng.
```

`FLOWBRIDGE_KEYCLOAK_ADMIN_PASSWORD` là biến mặc định; `--password-env` nhận tên
biến khác. HTTPS kiểm CA; HTTP chỉ cho loopback. Không log token hoặc response
body từ admin API. Client và Configure OTP action phải tồn tại/được enable.
Nếu OTP policy khác 6 số/30s/HmacSHA1, công cụ dừng để operator đánh giá thay vì
đổi policy của các ứng dụng khác. Flow tạo dở không tự xóa; kiểm tra và chỉ dọn
flow mới chưa bind do chính lượt provisioning đó tạo trước khi thử lại.

Local đã áp dụng và verify `flowbridge-browser-mfa-v2`, reload riêng các process
Core/Sales/Warehouse/Logistics thuộc ledger local để bật guard; Workspace gọi Core
nên nhận cùng kiểm tra MFA. Không restart worker, Workspace hoặc engine.
Không invalidation hàng loạt: token web/password-only cũ và refresh thiếu `acr=2`
bị API trả 401; người dùng đăng nhập lại qua Keycloak. Người dùng local chưa có
OTP, kể cả demo, tự đăng ký bằng app Authenticator ở lần đăng nhập kế tiếp.

Installer `scripts/deployment_render.py` hiện tạo confidential API/Hasura client,
không provision Portal browser client. Với stack đó, tạo public browser client
riêng, redirect URI đúng một callback `/`, webOrigins cụ thể, post-logout URI
hợp lệ rồi chạy công cụ MFA trên client đó; đồng thời thêm client vào allowlist
và MFA list của API. Không đổi API client thành public và không đưa secret ra web.

Recovery/admin tiếp tục dùng quy trình quản trị hiện hành trên flow riêng.
Mất thiết bị: operator xác minh danh tính, thực hiện quy trình reset OTP đã được
phê duyệt, sau đó Keycloak yêu cầu Configure OTP lại; không hạ OTP thành optional.

## Kiểm thử thực tế

- **PASS**: 267 test Platform + Core runtime offline; gồm token sai issuer/audience,
  expired/nbf/type/claim, thiếu MFA, token refresh password-only, giả header quyền,
  service account, provisioning additive/idempotent và phát hiện flow xung đột.
- **PASS**: 10 Vitest, build Portal; 10 Playwright API/IdP mock gồm SDK thật kiểm
  state sai, nonce sai và verifier PKCE khớp challenge. Đây là boundary tests.
- **PASS live**: `portal/tests/live/mfa.mjs` trên Keycloak local, Chrome context mới,
  native QR + enrollment sai/đúng, subsequent OTP sai/đúng không QR, low ACR không
  bypass, callback `acr=2`, Core identity/role/memberships giữ nguyên, giả admin403,
  reload, refresh, logout vô hiệu refresh và access token, missing/forged token401,
  web password grant400, token password-only từ harness bị Core401. Không mock API
  trong lượt này. Evidence an toàn: `.tmp/auth-mfa/live-evidence.json` (ignored).
- **PASS live chỉ đọc**: cấu hình apply lại không cần thay đổi, test account đã
  sạch OTP tạm; Core/Workspace/Sales/Warehouse/Logistics đều trả401 cho thiếu token
  và token password-only. Evidence: `.tmp/auth-mfa/final-config-and-api.json`.

Chạy trên fixture local đã có, không dùng tài khoản production:

```powershell
cd portal
$env:FLOWBRIDGE_MFA_E2E='1'
node tests/live/mfa.mjs
```

Runner xác minh đúng realm/client và `test-sales` thuộc ledger fixture, từ chối
nếu tài khoản đã có OTP; enroll credential tạm có nhãn riêng rồi chỉ dọn đúng
credential vừa tạo. Không ghi QR, provisioning URI, OTP, complete token, screenshot
hay trace. Chờ mã mới khi gặp chống replay. Baseline profile local có thể bổ sung
ở `.tmp/auth-mfa/baseline-profile.json` để so chính xác principal/memberships.
`tests/live/login.mjs` dùng cùng runner. Môi trường/fixture không sẵn sàng là BLOCKED,
không đổi account để ép PASS. Không chạy đồng thời hai runner dùng cùng fixture.

**Chưa nghiệm thu live**: fresh realm import, deployment production, APISIX và
Hasura xuyên suốt sau thay đổi. APISIX source hiện `routes: []`; chưa có gateway
route để kiểm. Hasura giữ permission integration hiện có; direct ingress phải
theo policy đang triển khai. Harness E2E cũ dùng token password-only nay cần
chuyển sang token browser MFA trước khi chạy các suite tích hợp nghiệp vụ.


## Giao diện thiết lập ứng dụng xác thực

Theme tùy biến riêng login-config-totp.ftl bằng TotpSetup, giữ toàn bộ giá trị
QR/secret/policy và action URL do Keycloak cấp. Trình duyệt gửi form native với
totp, totpSecret, userLabel, logout-sessions; cancel-aia chỉ xuất hiện với action
do ứng dụng khởi tạo. Không gọi Portal API, không ghi secret ở client storage.
QR giữ dữ liệu PNG gốc, nền trắng, kích thước dễ quét; mobile chuyển hai cột thành
hai bước dọc có cuộn. Manual setup dùng manualUrl/qrUrl native. Lỗi field được
sanitize, không hiện hai lần cùng global message. Các trang/password/OTP challenge
và MFA flow hiện hành không đổi bởi phần chỉnh giao diện này.

Đã build theme và cài JAR vào provider local đã dùng flowbridge, restart riêng
Keycloak; không đổi realm/client binding/credential. Bản cũ được backup ignored.
Mock browser tests kiểm tra layout, đường dẫn manual và native POST, không đăng ký
OTP thật. Tái kiểm chứng live MFA vẫn theo quy trình trong tài liệu này, không suy
ra full enrollment E2E chỉ từ kiểm tra UI/theme artifact.
