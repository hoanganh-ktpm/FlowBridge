# Changelog

## Unreleased

- Thêm chế độ API mẫu bật tường minh trong development/test: 28 operation Identity, Data/Storage, Workflow và events/notifications/audit; response đúng hợp đồng v0.2.0, chưa persistence hoặc xác thực OIDC thật.
- Thêm multipart upload, download bytes tổng hợp và metadata/hash nhất quán; dữ liệu fixture có ID dùng chung, request ghi không thay đổi fixture.
- Thêm mapping Gateway từ contract, ví dụ gọi đủ operation, Swagger schema sample và script kiểm tra HTTP có assertion. APISIX/engine thật để giai đoạn sau.
- Từ chối sample trong production hoặc khi có DSN; thêm cấu hình giới hạn upload và HTTP/contract tests không I/O engine.
- Thêm python-multipart 0.0.32, PyYAML 6.0.3, jsonschema 4.26.0 và referencing 0.37.0 qua manifest/lockfile; đóng gói contract cùng package, ví dụ và script trong source archive.
- Thêm lỗi API chuẩn, correlation ID có kiểm tra định dạng và cô lập request; che chi tiết validation/exception nhạy cảm.
- Thêm `/_ops/live` và `/_ops/ready` tách khỏi schema nghiệp vụ; readiness có deadline cấu hình, trả 503 khi bootstrap không có DB và chỉ chứng nhận runtime backend.
- Khai báo Starlette 1.8.0 trực tiếp để dùng giao diện ASGI đã có trong graph, không đổi phiên bản package upstream.
- Thêm app factory, cấu hình có validation, lifespan quản lý HTTP client/engine và session theo request; chạy module hỗ trợ cấu hình host/port/log level.
- Thêm unit test dữ liệu mock, cấu hình và vòng đời tài nguyên; chưa tạo/kết nối DB thật theo yêu cầu của nhóm. Đóng gói test trong source archive.
- Khởi tạo manifest Python 3.12, cấu hình uv/Ruff/pytest và package tại `backend/src/flowbridge`.
- Áp dụng GPL-3.0-or-later theo lựa chọn của nhóm; giữ LICENSE GPL v3 toàn văn.
- Bổ sung template môi trường, ignore rules và cấu hình LF.
- Áp dụng quy tắc code/review/license/test/release theo thể lệ hiện tại.

Chưa có release công khai; API hiện trả dữ liệu mẫu, chưa triển khai dữ liệu nghiệp vụ thật, migration hoặc kết nối engine.
