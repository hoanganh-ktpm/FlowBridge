# Platform

**Các adapter và năng lực kỹ thuật dùng chung cho ứng dụng.**

Khối của nhóm Identity/Infrastructure/Workflow. Source: backend/app/platform; test: tests/platform. Không import Core hoặc schema/business policy của ứng dụng.

| Module | Trách nhiệm / trạng thái |
|---|---|
| identity | Đã port token introspection, Keycloak Directory và IdentitySettings độc lập |
| http | Đã port error boundary, correlation, access logging và OpenAPI composition |
| gateway | Chỗ phát triển gateway integration; config APISIX thuộc infra/apisix |
| workflow | Chỗ phát triển Flowable adapter, task authorization và business links |
| audit_integration | Chỗ phát triển audit writer và liên kết trace; không giữ business audit thay domain |

## Sử dụng Identity

IdentitySettings chỉ có cấu hình IAM/CORS/proxy, không chứa cấu hình mua hàng, Agent Hub hoặc ML/RAG. Khi tích hợp FastAPI, đặt settings trên app.state.settings và một httpx.AsyncClient tại app.state.resources.http. Client cần timeout, TLS/CA đúng và follow_redirects=False. App sở hữu lifecycle của client.

Dependency authenticated_identity xác minh bearer qua IdP và trả VerifiedIdentity. Nó không tự tạo user/grant, suy phòng ban hoặc cấp quyền nghiệp vụ từ header. KeycloakDirectory(http, settings) chỉ đọc identity qua Admin REST bằng tài khoản directory riêng.

Có thể import/tái dùng app.platform trong ứng dụng khác mà không nạp app.core. Tests mock IdP, không yêu cầu Core DB hoặc Keycloak đang chạy. Chưa có runtime API identity/me hoặc workflow mới; adapter này cần được composition vào ứng dụng sau khi chốt contract.

Token người dùng không phải credential dài hạn cho workflow. Khi triển khai Workflow, tách initiated_by/executed_by, kiểm quyền User Task và chống tác động trùng.
