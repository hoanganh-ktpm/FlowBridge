# Changelog

## Unreleased

### Docker Integration Profile

- Chuyển Keycloak/Hasura test runtime sang Compose với image version/digest pin, loopback ports và Hasura metadata schema riêng; không tạo thêm database Supabase.
- Thêm generator secrets/TLS/test realm cục bộ, giữ runtime state khi chạy lại; bỏ yêu cầu JDK/Keycloak portable trên host.
- Core outbound HTTP sử dụng OIDC CA bundle đã cấu hình thay vì bỏ qua trust configuration; không tắt certificate verification.

### Automated Onboarding

- Bổ sung PostgreSQL CoreStore và native Core API/worker composition; dùng schema/contracts hiện hành, không memory fallback hoặc migration khi startup.
- Giữ LIVE PostgreSQL discovery/draft import độc lập với Hasura; không tự publish hoặc cấp quyền khi nhập manifest.
- Thêm bounded serialization retries, durable idempotency, worker lease fencing và internal short-lived Hasura JWT provider mặc định tắt tới khi xác minh auth/ingress.
- Kiểm PostgreSQL thật trên cluster TLS local riêng không Docker; thêm opt-in E2E harness và ghi rõ Hasura/SSO enforcement chưa nghiệm thu.

### Database và Migrations

- Bổ sung core_0004 thay metadata multi-organization bằng Core P0 một doanh nghiệp: principal/phòng ban, product/contract, department grant, publication jobs/audit và HTTP receipts.
- Thêm guarded realign-core với xác minh đích, khóa bảng, logical backup và schema check trong transaction; không thay history/schema/dữ liệu ba database nghiệp vụ.
- Core seed tùy chọn chỉ tạo draft registry; không tự cấp principal/admin/grant hoặc triển khai engine.

- Tiếp nhận 48 bảng và 6 Alembic revisions, tách models/migrations theo Core và từng Business API; giữ nguyên DDL và migration history.
- Hợp nhất CLI quản trị, fixtures tổng hợp, dependency group và CI database vào cấu trúc repository hiện hành; không đưa credential/PDF tham chiếu vào source phát hành.
- Tách kiểm thử cấu trúc offline và PostgreSQL integration/E2E; ghi rõ chênh lệch schema bàn giao với Core P0 và chưa ghép production persistence.

### Core Backend P0

- Thêm FastAPI composition và HTTP handlers cho vertical slice Data Sharing, không thay baseline API Contracts 0.2.0.
- Ghép canonical manifest validation/planning/atomic draft import; sửa legacy OWNER/target-read gate thành trusted IT administration và giữ guards cho published contracts.
- Triển khai command/repository transaction ports, current principal binding/grant policy, non-admin GraphQL read adapter và publication worker có lease/attempt/retry/CAS.
- Thêm component/HTTP tests qua adapters giả lập và database integration checklist. Chưa có ORM/migration/PostgreSQL adapter, credential provider production hoặc engine E2E; không dùng memory persistence production.

### API Contracts

- Thống nhất ba lớp quyền: IT/PLATFORM_ADMIN quản trị DX-OS xuyên phòng ban; consumer đọc theo grant; Business APIs kiểm quyền nghiệp vụ riêng. Bỏ department OWNER khỏi điều kiện quản trị/publish/approval; giữ metadata tổ chức tương thích DB và các technical guards.
- Đặc tả OpenAPI 3.1.1 phiên bản 0.2.0 cho P0 Data Sharing và các boundary P1; giữ method/path/operationId của catalog v0.1.1.
- Bổ sung declarative onboarding và canonical Data Contract giữ aliases, physical/wire types và relationships; tách DRAFT editable khỏi PUBLISHED immutable.
- Giới hạn read grants P0 theo department; đánh dấu Business DTOs và persistence gaps PENDING_DB_ALIGNMENT.
- Thêm kiểm tra specification/ref/schema/fixtures offline; chưa triển khai hoặc nghiệm thu database/runtime integration.

### Architecture

- Tổ chức backend theo `app/core` và `app/platform`, tách Business APIs và Portal.
- Khởi tạo module cho Application Registry, Data Sources, Data Products, Governance, Hasura Provisioner và Agent/Tools.
- Chuẩn hóa thư mục contracts, workflows, infrastructure và tests theo quyền sở hữu.

### Implemented Foundations

- Tái sử dụng và tách độc lập token introspection, Keycloak Directory và IdentitySettings.
- Tái sử dụng error boundary, correlation, access logging và OpenAPI composition.
- Giữ UI, PKCE/theme, presentation schemas và các kiểm thử frontend.
- Loại dependency Langflow, ML/RAG và runtime mua hàng cũ khỏi kiến trúc active.

### Documentation

- Cập nhật giới thiệu dự án, trạng thái triển khai, cấu trúc mã và hướng dẫn phát triển.
- Bổ sung tiêu chí bàn giao, chính sách nội dung Git và Pull Request template.
- Giữ thư mục chưa triển khai bằng package files hoặc `.gitkeep`.

### Pending Integration

- Business APIs, transaction nghiệp vụ và các quan hệ chia sẻ dữ liệu xuyên domain.
- Agent Runtime, Flowable adapter, BPMN Order Fulfillment và deployment stack.
- Chuyển API client của Portal sang contract mới và kiểm thử E2E với engine thật.
