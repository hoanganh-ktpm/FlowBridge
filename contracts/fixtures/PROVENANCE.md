# Provenance của fixtures dùng chung

`review-cases.json` được tạo tổng hợp cho API contract review ngày 22/10/2026, theo mô hình Warehouse/Sales/Logistics được mô tả trong hai PDF người dùng cung cấp. Không sao chép dòng dữ liệu thật, tài khoản, credential hoặc nội dung PDF vào fixtures.

UUID được tạo xác định bằng UUIDv5 từ alias demo; hostname thuộc `.example.test`; SKU `SP001`, order code và quantity đều là dữ liệu minh họa. `secret_ref` là tên tham chiếu giả, không chứa secret và không resolve được ra tài khoản thật. Query GraphQL là ví dụ thiết kế, không xác nhận schema Hasura đã tồn tại.

Mỗi case có `schema` (local JSON pointer), `valid` và `payload`. `valid: true` chỉ khẳng định JSON Schema chấp nhận shape; không khẳng định caller được cấp quyền, foreign key tồn tại, transaction thành công hoặc upstream đã áp dụng cấu hình. `valid: false` là input sai có chủ đích để kiểm rejection ở mức schema.

Các examples inline trong OpenAPI được lấy từ các fixture hợp lệ tương ứng. Khi đổi DTO, sửa schema/fixtures/example cùng nhau; schema chuẩn vẫn nằm trong OpenAPI. Riêng payload đăng ký Operation chứa input/output JSON Schema theo đúng API: fixture này mang snapshot schema đã resolve, được test đối chiếu với Domain target, không dùng làm nguồn định nghĩa DTO thứ hai. Bổ sung các ca rà soát v0.1.1 ngày 23/10/2026. Thử nghiệm behavior và engine thật vẫn phải triển khai theo `docs/API-CONTRACTS.md`.

Nội dung fixtures do dự án tạo: GPL-3.0-or-later, xem LICENSE và sidecar. Việc dùng hai tài liệu tham chiếu không tái cấp phép bản PDF hoặc dữ liệu bên ngoài.
