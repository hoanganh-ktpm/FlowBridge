# Domain Business APIs

Khối của nhóm nghiệp vụ: sales-api, warehouse-api, logistics-api. Mỗi ứng dụng có app/ và migrations riêng; test tập trung tại tests/domain/<domain> khi triển khai.

| Ứng dụng | Database | Logic sở hữu |
|---|---|---|
| sales-api | sales_db | Tạo đơn, quản lý trạng thái |
| warehouse-api | warehouse_db | Tồn kho, giữ/giải phóng hàng, đóng gói |
| logistics-api | logistics_db | Tạo vận đơn, bàn giao |

Mỗi ứng dụng đã có models tại `app/db/models.py`, migration history tại `migrations/versions/` và thiết kế `schema.dbml`; có thêm vertical slice chạy thật ở `/api/v1/<domain>/workflow/*`; chưa triển khai toàn bộ proposed P1 contracts. Xem [Business Workflow](../docs/WORKFLOW.md). Models import độc lập, không đọc source Core/service khác. Chốt Business API contracts trước; mỗi API tự kiểm quyền và transaction, không đọc database của nhau. Kho phải giữ hàng nguyên tử và idempotent; stock lookup trước đó không thay kiểm tra lúc ghi.

Công cụ quản trị và seed tổng hợp của bộ demo nằm tại `scripts/database/`, không phải dependency runtime của Business APIs. Xem [database handoff](../docs/database/README.md) về lệnh chạy và giới hạn kiểm thử.

Có thể dùng mock identity/Core/API phòng ban khác để code độc lập. Khi triển khai sẽ thêm manifest/runtime riêng cho mỗi ứng dụng; không đưa tất cả nghiệp vụ vào một module Core.
