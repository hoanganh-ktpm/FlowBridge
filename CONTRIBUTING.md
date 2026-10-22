# Đóng góp cho FlowBridge

## Bắt đầu

Đọc [README](README.md), [kiến trúc](docs/ARCHITECTURE.md) và [hướng dẫn phát triển](docs/DEVELOPMENT.md) trước khi thay đổi code. Cài đặt dependency theo manifest và lockfile đã có.

## Phạm vi sở hữu

| Khối | Source | Tests |
|---|---|---|
| Data Core | backend/app/core | tests/core |
| Platform | backend/app/platform | tests/platform |
| Business APIs | services/<domain>-api/app | tests/domain |
| Portal | portal/src | portal/src và portal/tests |

Mỗi domain sở hữu database/migration riêng. Core DB có một migration chain do người tích hợp điều phối. Engine quản lý schema nội bộ của mình.

## Quy trình thay đổi

1. Xác định module, mục tiêu và tiêu chí hoàn thành; thống nhất contract với các consumer bị ảnh hưởng.
2. Phát triển trên nhánh công việc, giữ thay đổi tập trung. Dùng fixtures/mocks tại boundary để kiểm thử độc lập.
3. Chạy các kiểm tra liên quan trong README; phân biệt unit, mock integration và E2E thật.
4. Gửi Pull Request mô tả hành vi thay đổi, validation, giới hạn và tác động đến API/database.
5. Review cùng chủ sở hữu module liên quan khi thay đổi contracts, migration, dependency hoặc cấu hình chung.

## Quy ước

- Python giữ ranh giới router → service/policy → repository/adapter; DTO tách model database.
- Platform không phụ thuộc schema nghiệp vụ; các ứng dụng giao tiếp qua contract/API.
- Kiểm quyền tại receiver; không tin identity/role do client tự khai.
- Thay đổi schema có migration, thay đổi dependency có manifest/lockfile tương ứng.
- Giữ notice và giấy phép upstream; bổ sung provenance cho dữ liệu mẫu.
- Không đưa secret, dữ liệu thật, dependency tải về hoặc build artifacts vào Git. Xem [repository policy](docs/REPOSITORY.md).

Commit nên mô tả phạm vi cụ thể, ví dụ `feat(core): add data source registration` hoặc `docs: clarify module ownership`. Không đưa kết quả test chưa chạy vào mô tả PR.

## Báo lỗi

Mô tả phiên bản/commit, bước tái hiện, kết quả mong muốn và thực tế. Loại bỏ token, mật khẩu và dữ liệu nhạy cảm khỏi log đính kèm.
