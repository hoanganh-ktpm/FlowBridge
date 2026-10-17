# ADR 0005 — Keycloak OIDC và introspection cho API Supabase

Ngày: 16/10/2026. Phạm vi: baseline Supabase, theo PLAN-SSO và yêu cầu code SSO/bỏ sample identity.

Backend vẫn headless. Keycloak sở hữu đăng nhập, password, PKCE và phiên; API xác minh access token qua RFC 7662 bằng confidential client. Tái sử dụng HTTPX client theo lifespan, timeout và kiểm issuer/audience/subject/expiry/nbf/token_type/client. Không cache kết quả cho phép truy cập. Service client registry phía server quyết định loại account, rồi policy đối chiếu với users.account_type trong PostgreSQL. Role/grant/resource do backend kiểm lại cho mỗi request.

Chọn introspection thay hướng JWT/JWKS của plan cũ cho lát triển khai này. Không tự triển khai JWT/JWKS verifier hoặc coi rotation test là đã chạy. IdP không sẵn sàng trả 503/504; thiếu/sai token trả 401. Registry client có thể giới hạn client phát token được chấp nhận. CORS dùng allowlist, không thay xác thực.

Directory adapter dùng Admin REST API chỉ đọc, client có `view-users` và `view-clients` trong realm ứng dụng; tách credential khỏi introspection. `UserProvision.identity_reference` là UUID user đã tồn tại ở Keycloak. API đọc metadata thật để liên kết issuer+subject; không tạo user/password ở IdP, không tự cấp grant hoặc reactivate hồ sơ. Vì không ghi IdP, transaction/idempotency chỉ cần bảo đảm phần DB, tránh distributed-write saga giả. Service account được đối chiếu qua endpoint service-account-user của client; không suy ra loại tài khoản từ username. Tạo tài khoản tại IdP dùng giao diện/công cụ upstream; provisioning hai chiều hoặc đăng ký tự động là scope sau.

Bootstrap CLI là thao tác operator có apply rõ ràng. Mặc định kiểm tra bằng transaction rồi rollback, kể cả outbox. ADMIN đầu tiên chỉ tạo khi chưa có ADMIN active, hoặc retry đúng ADMIN duy nhất đó; các lần link/grant khác cần UUID ADMIN active. Kết quả grant không đổi loại account/phòng ban/profile đang có. PostgreSQL advisory lock và row lock bảo vệ retry/concurrency; không có migration mới.

Hai browser client dùng `keycloak-js` qua npm, không copy source SDK. Test E2E gọi TCP listener và Keycloak + Supabase thật, không override authenticated_identity. Local certificate do OpenSSL tạo; HTTP client dùng CA cụ thể. Browser test pin SPKI của đúng certificate local trong browser tạm, không đặt ignoreHTTPSErrors hoặc bỏ TLS verification toàn cục. Trace/video/screenshot tắt để không lưu token/password.

Realm template khai báo SubMapper chuẩn cho access token/introspection; custom scope không dựa vào basic scope tự thêm. Native local profile dùng Keycloak dev/H2 độc lập, không tạo bảng IAM trong Supabase. Nó chỉ bind loopback; dev mode có listener HTTP bổ sung theo upstream nên không dùng profile này cho production. Production cần database IAM/persistence/TLS/secret management và network policy theo tài liệu upstream. Các image phải rà riêng trước phát hành; nghiệm thu native không chứng nhận image.

Dependency đã pin tại infra/identity/dependencies.json và clients/sso/package-lock.json: Keycloak 26.8.1, SDK 26.2.4, Playwright 1.64.1 (Apache-2.0), Temurin 25 native test (GPL với Classpath exception). File tải về giữ license/notice trong cache/tool directory bị ignore, không đóng gói vào source/wheel. Không thêm dependency Python.

Nguồn: [Keycloak OIDC](https://www.keycloak.org/securing-apps/oidc-layers), [JavaScript adapter](https://www.keycloak.org/securing-apps/javascript-adapter), [Admin REST](https://www.keycloak.org/docs-api/latest/rest-api/index.html), [cấu hình server](https://www.keycloak.org/server/configuration). Evidence thực tế nằm trong báo cáo SSO, không suy ra từ ADR.
