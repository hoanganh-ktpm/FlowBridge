# Logistics API

**Tạo vận đơn và xác nhận bàn giao.**

- app/: source ứng dụng.
- migrations/: schema và seed tổng hợp của logistics_db.
- Tests: tests/domain/logistics khi triển khai.
- Contracts: thư mục contracts ở gốc repository.

Hiện là khung ứng dụng, chưa có endpoint/runtime. API sở hữu transaction, authorization và idempotency cho nghiệp vụ của mình; không truy vấn database phòng ban khác. Manifest và entrypoint riêng sẽ được thêm cùng implementation.
