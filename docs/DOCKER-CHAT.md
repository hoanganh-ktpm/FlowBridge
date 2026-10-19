# Chạy FlowBridge bằng một lệnh Compose

## Cách chạy mặc định

Chỉnh file trước khi chạy, sau đó từ root FlowBridge:

```powershell
docker compose up
```

Chạy nền bằng `docker compose up -d`; xem bằng `docker compose ps -a`,
`docker compose logs --tail 80`, dừng bằng `docker compose down` (giữ volumes).
Docker Desktop Linux + Compose >=2.35.0 là đủ, không cần launcher/Python host.

| Nguồn cấu hình | Vai trò |
|---|---|
| compose.env | Ports, thư mục nguồn/cache, bật migration, demo accounts, ML/RAG/Hub |
| .env | DB thật và ML/RAG settings theo .env.example; biến cùng tên ưu tiên hơn compose.env |
| langflow/data/agent.env | Provider key/base URL; chỉ các trường cho phép được chuyển sang Langflow |
| langflow/data/langflow.db + secret_key | Snapshot nguồn có4flow ver2 và khóa đi cùng; nhận từ người bàn giao hoặc chuẩn bị/import theo Langflow |
| infra/dev/images.env + infra/chat/images.env | Manifest image chuẩn, pin tag/digest; Compose tự đọc |

Root compose.yaml dùng include với project_directory gốc để gộp base
infra/dev/compose.yaml, overlay chat và startup.yaml. Không sao chép manifest
image sang .env. Cấu hình startup mặc định ở compose.env; shell/.env có thể
đè giá trị. Tùy chỉnh xong chạy lại up; các service có pull_policy build để
build source hiện tại. Khi đổi nội dung file secret đã mount mà Compose không
recreate process, dùng down rồi up để nạp chắc chắn.

Thứ tự tự động: prepare → IAM healthy → database-init / Langflow → tài khoản
team / metadata flow → API healthy → client A/B. Lỗi job chuẩn bị sẽ chặn API/UI,
không báo ready giả. Prepare dùng SQLite backup API trong container khi tạo
volume Langflow lần đầu; volume đã có giữ flow/key riêng, không import lại.
Không cần tạo snapshot .cache trên host hoặc xuất password trước.

`FLOWBRIDGE_COMPOSE_MIGRATE=true` cho phép Alembic upgrade head lên DB được
cấu hình. `false` chỉ kiểm kết nối và head, dừng nếu schema chưa đúng.
Advisory lock PostgreSQL điều phối các lần startup đồng thời trên DB dùng chung.
Không seed dữ liệu. Không gọi model khi chuẩn bị/health/upgrade metadata.

`FLOWBRIDGE_TEAM_ACCOUNTS_ENABLED=true` tự đối soát bộ demo ở
[hướng dẫn ngắn](../HUONG-DAN-SU-DUNG-BACKEND.md), thông qua policy hiện có.
Nếu DB có ADMIN khác, điền `FLOWBRIDGE_TEAM_GRANTED_BY` bằng user_id ADMIN ứng
dụng trước khi up; không phải ID Keycloak. `FLOWBRIDGE_TEAM_TEST_PASSWORD`
chỉ dùng cho tài khoản mới, không reset password/OTP đã có. Giá trị mặc định
là password demo công khai theo chủ đích nhóm, không phải secret môi trường thật.

`FLOWBRIDGE_COMPOSE_UPGRADE_FLOWS=false` bỏ bước nâng metadata khi cần giữ bản
canvas đang chỉnh. ML/RAG mặc định tắt do cần artifact/cache/service grants
riêng. Hub mặc định bật, phụ thuộc migration0006; không tự tạo run/model call.
Không tự triển khai workflow/email/storage hoặc tuyên bố các engine đó đã sẵn sàng.

Tên project mặc định flowbridge-chat và tên volumes giữ nguyên để dùng lại
môi trường hiện có. Không đổi cổng SSO/API của volume đã chuẩn bị; profile sẽ
chặn thay đổi thay vì tự xoay state. Nếu cần một môi trường độc lập, cấu hình
COMPOSE_PROJECT_NAME trong .env và chuẩn bị DB/grants tương ứng.

Cơ chế Compose: [include](https://docs.docker.com/reference/compose-file/include/),
[startup dependencies](https://docs.docker.com/compose/how-tos/startup-order/).

### Kiểm chứng ngày20/10/2026

- `docker compose config --quiet` và đồ thị9service mặc định đạt, không cần
  flags manifest/overlay; file paths/build contexts trỏ đúng project root.
- Startup trên volumes hiện có đạt:5service dài hạn healthy,4job exit0;
  fingerprint IAM operator, fixture và backend chat secret giữ nguyên.
  UI A/B config, Swagger, readiness HTTP200; anonymous me401.
- Giải nén source tar.gz441members ngoài source tree, cung cấp private inputs
  sau khi audit archive, rồi chạy cùng một lệnh trên volumes trống: đạt.
  Dùng Supabase đã có schema/catalog/dữ liệu, không nhận nghiệm thu DB trống.
  IAM mới tạo lại đúng subjects,4user backend giữ nguyên IDs/grants; bản Langflow
  mới được import/upgrade metadata thành công. Không gọi model trong bài kiểm.
- 66unit liên quan đạt trên Linux build từ archive (3.84s); cùng nhóm test
  Windows đạt52+14. Chrome chấp nhận cả4mật khẩu demo trên IAM mới, dừng trước
  enroll OTP. Chưa chứng minh /me sau OTP cho user mới hoặc nghiệm thu ML/RAG.
- Namespace/volumes cold-test do lượt này tạo đã được dọn sau khi kiểm project
  name; bản sao .env private tạm đã xóa. Volumes stack chính được giữ để bật lại.
  Không thêm dependency/migration, không seed hoặc phát hành artifact/release.

## Launcher tương thích

Các helper `chat-docker.cmd` và `scripts/dev_stack.py` vẫn dùng được với Python3.12.
Chúng chọn base `infra/dev/compose.yaml` và overlay riêng, không tự chạy DAG startup
của root Compose. Quy trình bootstrap/E2E thủ công tại
[hướng dẫn chạy](../HUONG-DAN-CHAY.md#kiểm-thử-hệ-thống).
ML và RAG dùng [tài liệu ML](ML-SERVICE.md), [tài liệu RAG](RAG.md).
