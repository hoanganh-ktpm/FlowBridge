# Data Core

**Metadata, dữ liệu chia sẻ và chính sách quản trị của DX-OS.**

Khối của nhóm Data Core/Hasura. Source: backend/app/core; test: tests/core; contracts: contracts/.

| Module | Trách nhiệm |
|---|---|
| db | Kết nối dxos_core_db, model nền và một migration chain |
| applications | Phòng ban, đăng ký ứng dụng và chủ sở hữu |
| data_sources | Nguồn kết nối, secret_ref, schema discovery |
| data_products | Sản phẩm dữ liệu, contract/version và trạng thái công bố |
| governance | Access requests/grants, thời hạn, thu hồi và publication jobs |
| hasura_provisioner | Apply/reconcile metadata và permissions Hasura |
| agents | Agent/Operation/Tool Registry và executor |

[Hasura Provisioner](hasura_provisioner/README.md) có Metadata Client, source registration, product publication, SELECT reconciliation và publication worker. PostgreSQL adapter và native composition tại app.core.runtime đã kiểm bằng PostgreSQL integration; internal JWT provider mặc định tắt tới khi xác minh auth profile. Hasura/SSO E2E chưa nghiệm thu. Agents và phần P0/P1 ngoài vertical slice còn chờ triển khai; xem [Automated Onboarding](../../../docs/AUTOMATED-ONBOARDING.md). Không tạo một service deploy riêng cho mỗi dòng.

Core lưu metadata, không chứa dữ liệu nghiệp vụ Sales/Warehouse/Logistics. Module dùng Platform cho identity/HTTP boundary và adapter công khai; không import database nghiệp vụ. Có thể phát triển với PostgreSQL fixture, identity mock và mock engine APIs.

Migration chỉ thuộc dxos_core_db, đặt trong backend/app/core/db/migrations. Head core_0004 thay schema bàn giao cũ bằng 11 bảng P0 theo Core v0.2 + HTTP command receipts, cho một doanh nghiệp. Environment Alembic dùng chung ở scripts/database/migrations. CoreStore map schema hiện hành, không tự migrate khi startup. Xem [Database Setup](../../../docs/database/README.md), đặc biệt destructive replacement guard. Không dùng baseline Supplier Assistant hoặc sửa bảng nội bộ engine.

[Declarative Onboarding](../../../docs/ONBOARDING.md) nằm trong applications (manifest/compiler/orchestration/CLI), data_sources (read-only Inspector) và hasura_provisioner (pure payload builders). Validate/LIVE discovery/draft import chạy không Docker với PostgreSQL; publication và consumer access cần dịch vụ thật được cấu hình. Không thêm schema hoặc public API cho phần việc của nhóm khác.
