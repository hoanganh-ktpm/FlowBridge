# Làm việc với FlowBridge DX-OS

- Đọc README, docs/ARCHITECTURE.md và docs/DEVELOPMENT.md trước thay đổi kiến trúc. Nếu có docs/CHECKPOINT.md tại máy, đọc để tiếp tục công việc; file này là ghi chú cục bộ, không thuộc source phát hành.
- Code theo khối: backend/app/core; backend/app/platform; services/<domain>-api/app; portal. app.platform không phụ thuộc app.core hoặc nghiệp vụ. Import qua app để không che module platform của Python.
- Không import từ _archive. Mã dùng lại phải chỉnh dependency và đưa vào đúng module active; phần không dùng giữ trong archive bị ignore. Không tạo lại thư mục reuse.
- Mỗi domain sở hữu database/migration; Core sở hữu metadata/migration tại backend/app/core/db/migrations. Engine quản lý schema nội bộ của mình.
- Contracts là giao diện chung. Không đọc repository/schema nội bộ của khối khác; mock tại boundary để phát triển độc lập. Các service không import source của Core hoặc của nhau.
- Source/test tách riêng: tests/domain, tests/core, tests/platform, tests/e2e. Unit Platform không đòi database nghiệp vụ/Core hoặc engine thật.
- Source Python theo router → service/policy → repository/adapter, DTO tách ORM; chỉ thêm tầng/file khi có implementation. Không tạo rừng README hoặc nhiều placeholder file cho cùng trách nhiệm.
- Cấu hình engine chỉ nằm trong infra; adapter Python ở backend/app/platform hoặc backend/app/core. BPMN nghiệp vụ ở workflows. Không sao chép cấu hình sang nhiều nơi active.
- Không tự chạy migration, sửa realm, thay volume hoặc gọi tài khoản/model thật chỉ để kiểm tra cấu trúc.
- Giữ notice/giấy phép hiện có; không đưa secret/cache/binary/model vào source. Ghi đúng test/mock/E2E đã chạy. Chỉ commit/push hoặc triển khai khi nhiệm vụ được giao cho phép.
