# Contracts

Hợp đồng chung là điểm nối các khối. Nhóm thống nhất contract/fixture trước khi code integration.

- identity-context.schema.json: shape VerifiedIdentity sau xác thực, tương ứng adapter Platform hiện có. Đây là dữ liệu nội bộ tin cậy, không phải JSON client tự khai để nhận quyền.
- portal/schemas/: JSON presentation/event schema được giữ cho UI; không đại diện API registry/domain mới.
- fixtures/: dữ liệu tổng hợp dùng chung khi chốt Business APIs, data operation và grant model.

OpenAPI Domain/Core sẽ được thêm khi endpoint và DTO được thống nhất; không tạo OpenAPI rỗng rồi coi là API đã hoàn thiện. Những nội dung cần chốt: order_id/sku/status/errors/idempotency; Data Product/Grant; operation_ref/variables; danh tính human/service và actor workflow.

Không import hoặc đồng bộ tự động contract v1 trong archive. Portal hiện vẫn có boundary API/fixtures v1 ở portal/src/lib và portal/src/fixtures; cần chuyển sang contract mới khi API được triển khai.
