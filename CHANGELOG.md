# Changelog

## Unreleased

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

- Core/Domain APIs, database baselines và Hasura provisioning.
- Agent Runtime, Flowable adapter, BPMN Order Fulfillment và deployment stack.
- Chuyển API client của Portal sang contract mới và kiểm thử E2E với engine thật.
