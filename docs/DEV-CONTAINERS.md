# Chạy development/test bằng container

Phạm vi: backend FastAPI + Keycloak local, Supabase ngoài stack, client A/B và
test tự động. Ngày 17/10/2026 đã build/chạy trên Docker Linux, Compose config,
IAM/API/clients, 280 unit và SQL Identity đạt. Ngày 17/10 đã gỡ lệch migration;
19 SQL business test chạy trên host đạt, full SSO container chưa nghiệm thu.
Đây là môi trường development; PR00 vẫn còn task mở.

## Điều kiện

- Python 3.12 để chạy script điều phối; script host chỉ dùng standard library.
- Docker Engine Linux containers và Docker Compose **>= 2.35.0**; không cần Java,
  OpenSSL, Node hoặc Python dependencies trên host khi chạy hoàn toàn bằng container.
- DB development riêng có schema/dữ liệu cần dùng, cấu hình trong file dotenv riêng.
  Stack không tạo schema, migrate, seed, tải model hoặc sửa `.env` tự động.
- `test-sso`/`sql-business` yêu cầu revision DB bằng head migration có trong source.
  SSO còn cần role nền và ít nhất một product active, theo fixture hiện có.

Source đã bổ sung `0003_core_knowledge` từ commit gốc `99c9b69` trên nhánh nhóm.
DB hiện tại khớp head; fixture SQL/SSO đối chiếu chính xác Alembic heads,
không khóa hằng revision 0002. Xem [evidence đối chiếu](MIGRATION-0003-RECONCILIATION.md).
`up` chỉ kiểm kết nối và readiness, không xác nhận toàn schema đã tương thích.

## Lệnh chạy

Từ root FlowBridge; `python` là Python 3.12 trên PATH. Có thể thay bằng
`./.venv/Scripts/python.exe` ở Windows khi dùng môi trường hiện có.

Docker Desktop phải đang chạy ở chế độ Linux containers. Nếu vừa cài nhưng
terminal chưa tìm thấy `docker`, mở lại terminal. Với bản cài per-user Windows,
có thể bổ sung PATH cho riêng phiên PowerShell, không sửa cấu hình toàn máy:

```powershell
$dockerBin = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'
$env:PATH = "$dockerBin;$env:PATH"
```

```powershell
# Không gọi Docker, không đọc/in giá trị secret: xem tham số dự kiến.
python scripts/dev_stack.py up --dry-run --env-file .env
# Kiểm CLI/Engine/Compose; chưa bật dịch vụ hoặc thay đổi DB.
python scripts/dev_stack.py doctor --env-file .env
# Build và bật API + IAM, giữ state để dùng lại; không migrate/seed.
python scripts/dev_stack.py up --env-file .env --clients
python scripts/dev_stack.py status
python scripts/dev_stack.py logs
# Dừng đúng project này, giữ volumes/state/DB credentials.
python scripts/dev_stack.py down
```

Địa chỉ mặc định: API/Swagger [localhost:18082/docs](http://127.0.0.1:18082/docs),
IAM [localhost:19444](https://localhost:19444), client A/B ở
[8761](http://127.0.0.1:8761)/[8762](http://127.0.0.1:8762).
Trình duyệt thủ công có thể chưa tin CA local; test browser dùng SPKI riêng như
suite SSO hiện có, không đổi trust store hoặc tắt TLS verification đại trà.
Swagger tại `/docs` dùng cùng địa chỉ/cổng đang mở để gọi API. Nếu tab cũ vẫn gọi
`127.0.0.1:8000` trong khi dev chạy ở `18082`, cập nhật image API và tải lại trang.
Lấy access token từ client A/B bằng nút sao chép token, rồi nhập token vào **Authorize**
trong Swagger; tài khoản quản trị Keycloak không thay token của user ứng dụng.

`up` không tạo user/grant trong application DB; login IAM thành công chưa tự cấp quyền gọi API.
Test SSO tạo grant riêng trong fixture và dọn sau test.
Operator/test credentials ở volume IAM, không in trong log. Khi cần dùng UI local,
đọc riêng `operator.json` trong volume của project bằng công cụ quản trị container;
không chép nội dung đó vào issue/log/chat.

`--project flowbridge-<tên>` tách state. `--api-port`/`--iam-port` đổi hai cổng
đồng thời ở issuer, certificate profile và listener; 8761/8762 cố định theo client
SSO hiện có. Không dùng lại volume profile nếu thay cổng hoặc test mode: script
từ chối để tránh xoay secret/đổi issuer ngầm. Certificate local có hạn 30 ngày;
khi hết hạn tạo project mới hoặc quản lý thay thế tường minh, không reset realm.

Với `up` đã chạy, `.env` thay đổi chỉ được nạp khi container API được tạo lại:
chạy `down` rồi `up`. Không xóa volumes chỉ để reload cấu hình DB.

Khi cần khởi động lại toàn stack, dùng lệnh `down` rồi `up` như trên.
Restart đồng thời IAM/API bằng Compose đã làm API lỗi startup
DB trong kiểm thử thực tế; các container dùng chung namespace mạng cần được nối
lại sau IAM. Nếu gặp lỗi này, dùng chu trình down/up giữ volumes; không reset realm.

## Kiểm thử tự động

```powershell
# Không cần DB/IAM; build môi trường dev nhẹ, không cài model extras.
python scripts/dev_stack.py unit
# Chỉ SQL accounts/bootstrap; không cần bật server/IAM, fixture rollback.
python scripts/dev_stack.py sql-identity --env-file .env
# Toàn regression SQL nghiệp vụ: revision phải khớp trước khi ghi fixture.
python scripts/dev_stack.py sql-business --env-file .env
# IAM + SQL + browser thật trong project/volumes test mới.
python scripts/dev_stack.py test-sso --env-file .env
```

`test-sso` thực hiện build → prepare profile core mới (token 20 giây, OTP REQUIRED)
→ kiểm revision read-only → chờ IAM/API healthy → tạo fixture UUID riêng qua helper
hiện có → IAM+SQL test → Playwright A/B → cleanup DB → dừng/xóa **chỉ volumes của
project test mới do lần chạy tạo**. Mất điều kiện, assertion fail, engine unavailable
hoặc không collect được test đều trả exit khác 0. Không gọi mock để thay IAM/SQL/browser.

Lệnh không chấp nhận project test đã có container/volume; cổng đang bận sẽ dừng
trước tạo fixture. Dừng stack dev trước E2E hoặc chọn API/IAM ports khác; client
ports 8761/8762 vẫn phải trống. Không chạy đồng thời hai E2E trên cùng cổng.

Nhóm dev thêm đúng NumPy 2.5.4 và rank-bm25 0.2.2 đã có trong lock, để full unit
không phải cài toàn PyTorch/Chroma/TabICL. Runtime API không cài các extra đó.
Chỉ integration model/index riêng mới cần `ml`/`rag` theo tài liệu tương ứng.

## Khôi phục sau gián đoạn

Script lưu project, cổng, đường dẫn env và phase ở `.cache/dev-stack/<project>.json`;
không lưu giá trị env. `passed`, `failed-cleaned` và `recovery-required` khác nhau.
Nếu fixture còn pending, prepare từ chối đổi cấu hình DB để cleanup không nhắm sang DB khác.
Nếu cleanup DB hoặc dừng container không rõ kết quả, script trả lỗi và giữ volumes;
`fixture.json`/IAM state không bị xóa khi DB fixture còn cần đối soát.

```powershell
# Thay đường dẫn bằng manifest do lần chạy đã ghi.
python scripts/dev_stack.py status --recover .cache/dev-stack/flowbridge-test-<id>.json
python scripts/dev_stack.py up --recover .cache/dev-stack/flowbridge-test-<id>.json
python scripts/dev_stack.py cleanup-fixture --recover .cache/dev-stack/flowbridge-test-<id>.json
python scripts/dev_stack.py down --recover .cache/dev-stack/flowbridge-test-<id>.json
```

Không chạy lại `test-sso` để thay việc recovery. Khi bị kill cứng/mất điện, kiểm
container và outcome trước retry. Chỉ xóa volumes của project recovery sau khi
fixture cleanup được xác nhận; `down` thông thường luôn giữ volumes.

## Ranh giới cấu hình và mạng

- `infra/identity/realm.template.json` vẫn là nguồn realm duy nhất. `prepare_identity.py`
  sinh realm/runtime/public/operator; profile container chỉ gọi generator này.
- File env host chỉ mount read-only vào service prepare bằng Compose secret.
  Chỉ các trường `FLOWBRIDGE_DATABASE_*` đi vào volume `backend-config`, không
  vào image/layer/build args hoặc volume `identity-state` của IAM/browser.
- State volume được chuẩn bị owner 10001/group 0, file 0640/directory 0770 trên
  Linux. API/tooling chạy UID10001, Keycloak UID upstream, browser UID1000, group0
  cho phép đọc đúng volume. IAM/API mount state read-only; test helper/browser
  được ghi fixture/OTP. Đây là lưu secret local, không phải secret manager production.
- API/tools/browser/client dùng `network_mode: service:identity`: các tiến trình
  tách container nhưng cùng namespace mạng local để giữ issuer HTTPS `localhost`
  và verifier hiện có. Chỉ service identity publish cổng và đều bind host loopback.
  Không thêm host mapping, proxy Python hoặc thay token/issuer policy. Management
  health HTTP9000 chỉ nội bộ, không publish. Đây chưa phải Gateway public/private.
- Image references + digest nằm ở `infra/dev/images.env`; có thể override bằng env
  đã được kiểm. Script truyền manifest tường minh và `--project-directory`, nên
  chạy được từ ngoài checkout. Không dùng `.env` secret làm Compose interpolation file.
- ML/RAG runtime, PostgreSQL local, storage/scanner/search/workflow/broker chưa được
  thêm. Các engine sau có profile riêng khi đã chọn và nghiệm thu upstream.

## Evidence và phần còn lại

Kết quả từng kiểm tra ghi trong [infra/dev/README.md](../infra/dev/README.md).
Còn full SSO/business trong container, recovery sau fixture có tác động thật,
audit toàn image packages/licenses/secrets và build container từ archive sạch.
Không suy ra các bước này đã đạt từ healthcheck hoặc unit test.
