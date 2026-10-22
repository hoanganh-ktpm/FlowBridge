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

[Hasura Provisioner](hasura_provisioner/README.md) đã có Metadata Client, source registration, product publication và SELECT permission reconciliation; kiểm thử độc lập bằng HTTP mock/fake repositories. Chưa có Core DB adapters, trusted admin authorization provider hoặc live engine integration. Các module Core còn lại vẫn là scaffold. Khi viết mới, đặt router/schema/service/repository/model trong đúng module; không tạo một service deploy riêng cho mỗi dòng.

Core lưu metadata, không chứa dữ liệu nghiệp vụ Sales/Warehouse/Logistics. Module dùng Platform cho identity/HTTP boundary và adapter công khai; không import database nghiệp vụ. Có thể phát triển với PostgreSQL fixture, identity mock và mock engine APIs.

Migration chỉ thuộc dxos_core_db, đặt trong backend/app/core/db/migrations. Không dùng lại baseline Supplier Assistant hoặc sửa bảng nội bộ engine. Chưa có baseline/env Alembic để chạy.

[Declarative Onboarding](../../../docs/ONBOARDING.md) nằm trong applications (manifest/compiler/orchestration/CLI), data_sources (read-only Inspector) và hasura_provisioner (pure payload builders). Có thể validate/preview không Docker; Core persistence, authorization và live apply vẫn chờ adapters. Không thêm schema hoặc public API cho phần việc của nhóm khác.
