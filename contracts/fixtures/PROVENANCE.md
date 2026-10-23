# Provenance của fixtures dùng chung

`review-cases.json` chứa các ví dụ tổng hợp cho API Contracts 0.2.0. Bộ hiện tại thay thế fixtures v0.1.1; lịch sử phiên bản trước được giữ trong Git. Không sao chép dòng dữ liệu thật, tài khoản, credential hoặc nội dung tài liệu tham chiếu vào fixtures.

UUID, hostname, SKU, mã nghiệp vụ và số lượng đều là dữ liệu minh họa. `secret_ref` là tên tham chiếu giả, không chứa secret và không chứng minh khả năng resolve ra tài khoản thật. Mọi cấu hình hoặc query mẫu không xác nhận schema Hasura đã tồn tại.

Mỗi case có `name`, `schema` (tham chiếu schema cục bộ), `valid` và `value`. `valid: true` chỉ khẳng định JSON Schema chấp nhận shape; không khẳng định caller được cấp quyền, foreign key tồn tại, transaction thành công hoặc upstream đã áp dụng cấu hình. `valid: false` là input sai có chủ đích để kiểm rejection ở mức schema.

Khi đổi DTO, sửa schema, fixtures và examples liên quan cùng nhau; fixtures không phải nguồn định nghĩa DTO thứ hai. Suite `tests/contracts/test_api_contracts.py` kiểm tra refs, shape, tính nhất quán và policy được đặc tả. Thử nghiệm behavior và engine thật vẫn phải triển khai theo [API Contracts](../../docs/API-CONTRACTS.md).

Nội dung fixtures do dự án tạo: GPL-3.0-or-later, xem LICENSE và sidecar. Việc dùng hai tài liệu tham chiếu không tái cấp phép bản PDF hoặc dữ liệu bên ngoài.
