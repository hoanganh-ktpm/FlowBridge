# Evidence Identity core — 16/10/2026

Baseline: `huuhuandev_features`, HEAD `d03450c4ca255eeb2ef60bb75e01d17e2cd12174` + working-tree Identity core. Không commit/push/deploy hoặc migration mới. Thiết kế/binding: [ADR](IDENTITY-CORE-ADR.md); cách chạy: [Identity core](IDENTITY-CORE.md).

## Kiểm tra

| Phạm vi | Kết quả |
|---|---|
| Unit toàn bộ trước bổ sung boundary errors | 241 passed in11.34s, session61671 exit0 |
| Identity unit cuối | 23 passed in3.45s, không skip; thêm pool timeout và lỗi/timeout IAM fail closed |
| Unit toàn bộ cuối | **244 passed in9.47s**, session56602 exit0, không skip |
| IAM + Supabase integration mới | 1 passed in3.29s, lệnh exit0; service token và introspection thật, directory/client binding, principal SQL, revoke/disable và rollback |
| Browser E2E cuối | **5 passed (2.9m)**, session74680 exit0, không skip/auth override |
| Ruff | Lint + format-check **81 file đạt**; git diff --check đạt |
| Lock | uv lock --check --offline, **142 packages**, đạt; không dependency mới |
| Build | Source tar.gz/wheel offline/no-build-isolation đạt, **157/62 members**; source/wheel secret/bundling audit đạt |
| Clean rebuild | Giải nén ngoài checkout, build lại source+wheel offline bằng toolchain đã có: đạt |
| Wheel import | Python -I import wheel rebuilt ngoài checkout: DB+core48 / ML+core53 operations; unique operationId, mọi local ref resolve, config/security/gateway mapping đúng; không khởi động lifespan/DB/IAM/model |

Unit mock chỉ chứng minh policy/HTTP invariants. Human metadata trong SQL revoke test được tiêm riêng; browser suite chịu trách nhiệm xác thực human thật. SQL integration dùng service access token được cấp và introspect qua IAM thật, không giả service identity.

## Browser/IAM thật

Profile mới `.cache/identity-core-verification`, Keycloak26.8.1 HTTPS19444, API TCP18082, browser A/B8761/8762 và Supabase thật. Browser tin SPKI local; Node/API tin CA riêng, không thay trust store hệ thống. Realm import sạch dùng password + OTP REQUIRED, PKCE S256, scope identity:read và access token20 giây cho test expiry.

Hai test core chứng minh public config không secret; me đúng user/tenant/role/permission dù header giả; missing/bad/ID/refresh token401; service gọi human me403; expiry thật401; redirect ngoài allowlist bị từ chối và PKCE verifier sai bị invalid_grant. Ba test SSO cũ tiếp tục đạt trên profile MFA: A→B không nhập lại mật khẩu, refresh/logout, PR persistence/idempotency/ownership, ADMIN verified-link và service role isolation.

## Lỗi trong quá trình kiểm

Lượt đầu hai core test timeout vì helper tìm input submit thay button của theme v2. Lượt thứ hai 3pass/2fail: callback PKCE test bắt sai URL và đăng nhập lại tái dùng OTP cùng timestep. Đã sửa helper bắt callback query/fragment, chờ timestep OTP mới; không nới Keycloak guard hoặc đổi API status. Lượt cuối năm test pass là evidence nghiệm thu.

Sandbox Windows không mở được loopback asyncio/socketpair hoặc IAM selector. Đã dừng đúng pytest task bị kẹt; kiểm mạng/test sau đó chạy ngoài sandbox. Lỗi môi trường và các lượt fail không tính là pass.

## Cleanup và giới hạn

Fixture riêng5 user/grant/department và PR/outbox/idempotency sinh bởi browser đã cleanup thành công bằng identity_fixture.py đúng profile, fixture.json đã gỡ. Các thay đổi SQL revoke/disable rollback. API36068 và IAM35672 đã dừng; kiểm không còn listener19444/18082/8761/8762. Không đụng `.env`, fixture identity-final, user/grant có sẵn; không seed/reset/migrate. IAM DB/OTP test state giữ ở ignored profile, không đóng gói. Trace/video/screenshot và snapshot trang lỗi tắt để tránh lưu OTP/token.

Single-tenant binding; không chứng nhận multi-tenancy, mobile native, production TLS/persistence/HA hoặc image. UserContext qua IAM-issued token có code/unit giao quyền, chưa có receiver Data/Workflow production để nghiệm thu xuyên engine. JobContext có policy boundary/registry port và unit cases, **chưa có registry production**, thiếu adapter503; CODE-01-02.04 và CP-002 vẫn mở. Không dùng kết quả core identity để nhận Data/Files/Workflow/Gateway đã đạt.

Build/audit ở trên là verification cục bộ, chưa tạo release. Archive lần đầu chứa evidence trung gian; gói cuối được build lại sau khi chốt tài liệu. Không audit OCI image hoặc inference model trong task Identity.
