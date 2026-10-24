# Changelog

## Unreleased

### Local PostgreSQL và Business Workflow E2E

- Thêm PostgreSQL 16/Hasura local, generator 365 ngày và sáu Data Product views; giữ secrets, TLS, runtime evidence và dữ liệu sinh ngoài Git.
- Triển khai vertical slice APIs Sales/Warehouse/Logistics với transaction, quyền theo subject/org, idempotency và hai bảng delivery workflow qua migration additive.
- Chạy Flowable OSS 7.2.0 trên flowable_db trong PostgreSQL local; hoàn thiện order fulfillment với Data Action đọc Hasura, reservation, Warehouse User Task và Logistics delivery event.
- Thêm relay xác thực webhook Hasura rồi ký HMAC, giữ IT event config tách Workflow subscription; sửa query Flowable dùng processBusinessKey và kiểm tra correlation trả về.
- Nghiệm thu 841 regression tests, 43 PostgreSQL tests và 5 live Business Workflow tests; restart/retry/event replay không gây ghi trùng, checksum lịch sử giữ nguyên. data.update vẫn unavailable, tách khỏi kết quả Business API writes.

### Workflow Engine (Layer 1, Flowable OSS)

- Mount Workflow router vào native Core API; dùng Core principal policy, giới hạn assignee và không trả process variables. Nối System Data Actions vào Data Product/grant/Hasura read pipeline hiện có, không mở quyền ghi nghiệp vụ.
- Thêm BPMN mẫu order_fulfillment tại workflows/ và compose Flowable tối thiểu tại infra/flowable dùng Supabase qua JDBC+TLS, schema flowable_runtime riêng, pool giới hạn.
- Thêm Hasura event-trigger provisioning helper cho workflow subscriptions (control-plane only, webhook HTTPS).
- Bổ sung CoreStore adapters và migration core_0006 cho action definitions/durable dispatch inbox, không blind retry intent chưa rõ kết quả. Sửa lỗi nhân đôi tên DROP CHECK của core_0005 theo phê duyệt và bind audit details rỗng thành SQL NULL thay vì JSON null.
- Kiểm chứng runtime Flowable 7.2.0 trên Supabase bằng token Keycloak/native API, HTTP Service Task/Gateway/User Task/message và restart. Tách rõ offline doubles, runtime probe và opt-in Data Sharing Workflow E2E tại docs/WORKFLOW.md.

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
