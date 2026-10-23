# Nội dung repository

## Đưa vào Git

- Source backend, Business APIs và Portal.
- OpenAPI/JSON Schema, migration và seed/fixtures tổng hợp có provenance.
- Unit/integration/E2E tests, cấu hình kiểm thử và placeholder cần thiết.
- Dependency manifests và lockfiles để tái lập môi trường.
- Template cấu hình không chứa credential; .env.example.
- README, tài liệu kỹ thuật, license/notice và hướng dẫn đóng góp.

## Giữ cục bộ

- _archive, hướng dẫn agent tại AGENTS.md và ghi chú vận hành cục bộ như docs/CHECKPOINT.md, docs/HASURA_AUDIT.md.
- .env thực, private keys, certificates và credential runtime.
- .venv, node_modules, công cụ tải về và cache.
- Build output, test reports, logs và coverage.
- Database dumps, runtime databases, uploads và model weights.
- Tài liệu tham chiếu ngoài source tại docs/reference.

.gitignore không thay đổi lịch sử Git hoặc tự bỏ track file đã có. Khi chuyển mã cũ khỏi cây active, commit ghi nhận xóa đường dẫn cũ; các phiên bản đã commit vẫn có trong lịch sử. Archive cục bộ không đi theo clone hoặc source distribution.

## Thư mục chưa triển khai

Git lưu file, vì vậy thư mục chưa có source cần .gitkeep. Package có __init__.py đã được giữ trong cây Git. Không tạo file runtime/BPMN giả để lấp thư mục.

Khi có implementation, có thể bỏ .gitkeep nếu thư mục đã có file được track. Kiểm tra cây staged bằng git diff --cached --name-status trước khi commit.

## Dữ liệu mẫu

Fixtures phục vụ preview/tests phải là dữ liệu tổng hợp, có nhãn và provenance. Không đưa dữ liệu người dùng cung cấp hoặc dữ liệu nghiệp vụ thật vào source chỉ vì schema/code có giấy phép GPL.

Không ignore toàn bộ *.sql, *.sh hoặc docs: migration, script vận hành và tài liệu cần thiết thuộc source. Chỉ loại dữ liệu runtime, artifacts và nội dung cục bộ tương ứng.
