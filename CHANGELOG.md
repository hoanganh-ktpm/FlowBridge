# Changelog

## Unreleased

### API Contract Review Candidate v0.1.1

- Thêm OpenAPI Core/Domain/Platform và components dùng chung, JSON Schema resolved identity, fixtures tổng hợp cùng kiểm tra contract.
- Ghi rõ trạng thái review, phân chia P0/P1, quyền đọc/execute/ghi, publication revisions, idempotency và workflow trung lập engine theo thiết kế database v0.2.
- Rà soát v0.1.1: bổ sung governed data read và tra cứu workflow link; siết identity, mapping Business API, stale approval và compensation race. Có 84 operations, 63 fixtures và báo cáo/PDF mô tả.
- Đây là specification và tiêu chí nghiệm thu; chưa triển khai endpoint, migration hoặc runtime integration.

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
- Agent Runtime, workflow adapter trung lập engine, Order Fulfillment và deployment stack.
- Chuyển API client của Portal sang contract mới và kiểm thử E2E với engine thật.
