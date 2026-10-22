# Warehouse API

**Quản lý tồn kho, giữ/giải phóng hàng và xác nhận đóng gói.**

- app/: source ứng dụng.
- migrations/: schema và seed tổng hợp của warehouse_db.
- Tests: tests/domain/warehouse khi triển khai.
- Contracts: thư mục contracts ở gốc repository.

Hiện là khung ứng dụng, chưa có endpoint/runtime. API sở hữu transaction, authorization và idempotency cho nghiệp vụ của mình; không truy vấn database phòng ban khác. Manifest và entrypoint riêng sẽ được thêm cùng implementation.
