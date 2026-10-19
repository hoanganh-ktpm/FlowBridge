# Hướng dẫn chạy FlowBridge

Quy trình chung cho Keycloak, FastAPI, Langflow và React trong stack Docker
`flowbridge-chat`. PostgreSQL/Supabase nằm ngoài stack. Chạy lệnh tại thư mục gốc
repo; state và credential của mỗi môi trường được giữ riêng.

## Chuẩn bị môi trường

- Docker Desktop với Linux Engine và Compose >=2.35.0.
- Không cần Python/Java/Node host khi dùng Compose. Launcher `.cmd` thủ công mới cần Python3.12.
- Database PostgreSQL/Supabase và `.env` hợp lệ theo `.env.example`.
- Bốn flow ver2 trong Langflow cùng một owner active. Cần cả
  `langflow/data/langflow.db` và `langflow/data/secret_key` tương ứng. Máy mới xem
  [chuẩn bị flow](langflow/README.md#chuẩn-bị-flow-trên-máy-mới).
- Provider/model trong `langflow/data/agent.env`, theo `langflow/agent.env.example`.
- ML/RAG: checkpoint/context, model cache, pgvector, service grant và analysis task
  theo [ML](docs/ML-SERVICE.md) và [RAG](docs/RAG.md).

Clone source không bao gồm state/credential. Chuyển môi trường cần bàn giao DB
Langflow và khóa qua kênh riêng, hoặc import flow mới.

Chỉ tạo `.env` nếu chưa có:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

Điền `FLOWBRIDGE_DATABASE_URL`, hoặc các trường thành phần:

```dotenv
FLOWBRIDGE_DATABASE_HOST=YOUR_SUPABASE_POOLER_HOST
FLOWBRIDGE_DATABASE_USER=postgres.PROJECT_REF
FLOWBRIDGE_DATABASE_PORT=5432
FLOWBRIDGE_DATABASE_NAME=postgres
FLOWBRIDGE_DATABASE_PASSWORD='YOUR_PASSWORD'
FLOWBRIDGE_DATABASE_SSL=require
```

URL có giá trị được ưu tiên hơn trường thành phần. Muốn dùng trang quản lý tài
liệu, thêm `FLOWBRIDGE_RAG_MANAGER_ENABLED=true`; cần RAG PostgreSQL và ADMIN.
`FLOWBRIDGE_CHAT_RAG=true` trong `compose.env` bật retrieval, không tự bật manager.

## Khởi động

1. Chỉnh `compose.env` cho cổng, bật/tắt migration, tài khoản test và ML/RAG/Hub.
   `.env` giữ kết nối Supabase, `langflow/data/agent.env` giữ provider/model.
2. Mở Docker Desktop, chờ Linux Engine rồi tại root repo chạy:

   ```powershell
   docker compose up
   ```

   Dùng `docker compose up -d` nếu chạy nền. Compose tự build, chuẩn bị cấu hình,
   chờ IAM, kiểm/nâng schema, tạo tài khoản team, nâng metadata flow rồi bật API
   và UI A/B. Không cần chạy lệnh tạo tài khoản hoặc launcher riêng.
3. Mặc định `FLOWBRIDGE_COMPOSE_MIGRATE=true` cho phép Alembic upgrade head trên
   đúng Supabase trong `.env`. Chọn `false` nếu chỉ muốn kiểm schema hiện có.
   Không seed, reset hoặc stamp. DB mới cần dữ liệu nền theo
   [quản lý database](alembic/README.md).
4. ML/RAG mặc định tắt do cần artifact/cache/service grant thật. Khi đã chuẩn bị,
   đổi `FLOWBRIDGE_CHAT_ML=true` và `FLOWBRIDGE_CHAT_RAG=true` trong `compose.env`.
   Không tự tải weights; thiếu artifact phải xử lý lỗi trước khi dùng.
5. Mở [FlowBridge](http://127.0.0.1:8761/), đăng nhập bằng tài khoản test ở dưới.

| Thành phần | Địa chỉ mặc định |
|---|---|
| FlowBridge | http://127.0.0.1:8761/ |
| Swagger | http://127.0.0.1:18082/docs |
| Langflow editor | http://127.0.0.1:7861/ |
| Keycloak Admin | https://localhost:19444/admin/ |

Browser cần tin CA local của đúng profile theo [SSO](docs/SSO.md); giữ TLS
verification. Keycloak và Langflow editor dùng tài khoản riêng.

## Tài khoản và quyền

Compose mặc định tự tạo bộ demo dùng chung, không reset password/OTP của user cũ:

| Tài khoản | Mật khẩu mặc định | Quyền |
|---|---|---|
| `tester01` | `FlowBridgeTest@2026` | EMPLOYEE |
| `tester02` | `FlowBridgeTest@2026` | EMPLOYEE |
| `tester03` | `FlowBridgeTest@2026` | EMPLOYEE |
| `team-admin` | `FlowBridgeTest@2026` | ADMIN, không tự có EMPLOYEE |

Có thể chỉnh `FLOWBRIDGE_TEAM_TEST_PASSWORD` trước khi tạo user. Nếu DB có ADMIN
khác, điền `FLOWBRIDGE_TEAM_GRANTED_BY` bằng UUID ADMIN ứng dụng trước khi chạy.
Giữ cùng issuer/port và dùng chung Supabase để cùng user/quyền backend; OTP và
thay đổi mật khẩu của IAM vẫn riêng trên từng máy.

Cách đăng nhập/test UI/Swagger tại [hướng dẫn ngắn](HUONG-DAN-SU-DUNG-BACKEND.md).
Thiết kế startup/config chi tiết tại [Docker Compose](docs/DOCKER-CHAT.md).

Các tài khoản local cũ và quản trị Keycloak có credential riêng. Khi cần, xuất
credential Keycloak của stack vào file riêng bị Git ignore:

```powershell
.\chat-docker.cmd credentials
```

Mở `.cache/chat-stack/flowbridge-chat/credentials.json` bằng editor:
`keycloak_users` chứa user local, `keycloak_admin` chứa tài khoản quản trị IAM.
Không gửi file hoặc QR/OTP lên Git, chat hay issue.

User IdP như `flowbridge-admin`, `flowbridge-employee`, `flowbridge-other` không tự
có grant trong database mới. Liên kết bằng issuer+subject và cấp quyền theo
[SSO](docs/SSO.md#3-liên-kết-user-và-cấp-quyền-có-chủ-đích). Chat cần EMPLOYEE;
ADMIN không tự có quyền đó. Keycloak có thể yêu cầu thiết lập OTP lần đầu.

CLI bootstrap có trong image tooling. Ví dụ xem trước cấp EMPLOYEE khi đã có ADMIN:

```powershell
docker compose --project-name flowbridge-chat --env-file infra/dev/images.env --env-file infra/chat/images.env -f infra/dev/compose.yaml -f infra/chat/compose.yaml run --rm --no-deps tools python scripts/container_chat.py bootstrap --subject UUID_USER_KEYCLOAK --grant EMPLOYEE --department UUID_PHONG_BAN --granted-by UUID_ADMIN_UNG_DUNG
```

Thay UUID bằng định danh đã kiểm. CLI rollback mặc định; thêm `--apply` để ghi sau
khi kiểm kết quả. Chưa có ADMIN dùng quy trình bootstrap đầu tiên trong SSO.md.
Quản trị Keycloak không thay quyền quản trị ứng dụng.

Editor dùng owner của flow trong DB đã import. Mật khẩu giữ từ DB nguồn, không bị
env startup ghi đè. Nếu môi trường đã đổi mật khẩu, dùng bản ghi credential riêng
của môi trường đó. Owner khác có workspace riêng; đăng ký editor không tự cấp
quyền xem flow của người khác.

## Sử dụng nghiệp vụ và chat

1. Đăng nhập, vào **Trợ lý AI**.
2. Chọn PR bằng **Tải yêu cầu của tôi** khi câu hỏi gắn yêu cầu mua hàng; để
   **Tra cứu chung** nếu hỏi danh mục.
3. Nhập câu hỏi, ví dụ: `Liệt kê 2 sản phẩm đang hoạt động, gồm mã và tên.`
4. Chờ kết quả. **Tải lại hội thoại** đọc lịch sử. Nếu chưa rõ kết quả gửi, dùng
   **Kiểm tra lại lượt gửi** để giữ nguyên body và Idempotency-Key.

Backend kiểm token/quyền, truyền caller theo request tới Langflow và tool. Không
nhập API key Langflow vào chat. Hội thoại thật gọi model và có thể phát sinh phí.
Backend chưa đưa lịch sử trước vào model; mỗi câu hỏi cần đủ thông tin nghiệp vụ.

RAG trong chat cần PR thuộc caller và analysis task thật cùng revision/cutoff hợp
lệ, cùng service token AI_SERVICE. Không dùng JobContext mẫu hoặc user token thay
service token. Quản lý tài liệu cần ADMIN; đợi job `ready` rồi tra cứu bằng task mới.

## Dừng, cập nhật và dữ liệu bền vững

```powershell
docker compose ps -a
docker compose logs --tail 80
docker compose down
docker compose up
```

`down` giữ volume. Đổi `.env`/provider settings dùng `down` rồi `up` để process nạp
lại. Giữ cùng project và cổng trong file để dùng lại state. `COMPOSE_PROJECT_NAME`
trong `.env` chọn môi trường độc lập; không đổi cổng của profile đã có.

Lần đầu Compose chụp DB Langflow bằng SQLite backup API bên trong container và
giữ khóa nguồn đi cùng, rồi import một lần vào volume. Bản Docker có
state độc lập; sửa flow native sau đó không tự đồng bộ. Metadata ver2 cũ được nâng
trên bản Docker, backup tại `/var/lib/langflow/flowbridge-backups` và kiểm concurrent
edit. Muốn đổi snapshot nguồn dùng project mới, không ghi đè volume đang dùng.

Bridge/API key nội bộ được sinh và giữ trong volume. Langflow không mount DB/IAM
config; launcher chỉ chuyển provider settings từ `agent.env`, không sao chép bearer
hoặc JobContext tĩnh. Cache `.cache/models` mount read-only tại `/models`, context
`.cache/ml` tại `/ml-context`; đổi đường dẫn trong `compose.env`.
Image references được pin trong `infra/*/images.env`.

## Xử lý lỗi

| Hiện tượng | Kiểm tra |
|---|---|
| Docker thiếu Linux Engine | Mở Docker Desktop, chờ Engine; kiểm `wsl -l -v`. |
| Không mở được 8761 | Xem `docker compose ps -a`, `docker compose logs`, xung đột cổng. |
| Lỗi chứng chỉ Keycloak | Tin CA local đúng profile, giữ TLS verification. |
| DB revision/connectivity lỗi | Kiểm Supabase và migration theo source; không reset/stamp. |
| Login nhưng 401/403, tab chat khóa | Kiểm phiên, issuer+subject, user active và EMPLOYEE trong DB. |
| Editor trống/chờ duyệt | Dùng owner của flow; quản trị viên editor kích hoạt user mới. |
| Chat 503/504 | Kiểm Langflow healthy, bridge, provider key/model, log. |
| RAG chưa khả dụng | Kiểm pgvector/index, AI_SERVICE, PR/task/cutoff thật. |
| ML chưa sẵn sàng | Kiểm checkpoint/context/cache theo ML-SERVICE.md. |
| UI chưa cập nhật | Chạy `up` để build/recreate client, tải lại browser. |
| Thiếu DB/khóa Langflow | Import flow hoặc bàn giao DB và khóa cùng nhau. |

## Kiểm thử hệ thống

Kiểm cấu hình và regression offline:

```powershell
docker compose config --quiet
uv run --locked pytest tests/unit -q
npm --prefix clients/sso run test:ui
```

E2E dùng browser, Keycloak, PostgreSQL và Langflow thật. Dùng project riêng với
`.env.test` trỏ DB test có dữ liệu nền, quyền và artifact hợp lệ. Các lệnh tạo
fixture, gọi model thật rồi cleanup; không dùng profile chứa user/dữ liệu đang
giữ để sử dụng hằng ngày. Client A vẫn dùng cổng 8761 nên dừng stack khác trước.

```powershell
.\chat-docker.cmd up --project flowbridge-chat-e2e --env-file .env.test --ml --rag
docker compose --project-name flowbridge-chat-e2e --env-file infra/dev/images.env --env-file infra/chat/images.env -f infra/dev/compose.yaml -f infra/chat/compose.yaml run --build --rm --no-deps tools
docker compose --project-name flowbridge-chat-e2e --env-file infra/dev/images.env --env-file infra/chat/images.env -f infra/dev/compose.yaml -f infra/chat/compose.yaml run --build --rm --no-deps browser npx playwright test --config chat.config.mjs
docker compose --project-name flowbridge-chat-e2e --env-file infra/dev/images.env --env-file infra/chat/images.env -f infra/dev/compose.yaml -f infra/chat/compose.yaml run --rm --no-deps tools python scripts/container_dev.py cleanup
.\chat-docker.cmd down --project flowbridge-chat-e2e --env-file .env.test --ml --rag
```

Nếu test lỗi, vẫn đối soát và cleanup đúng manifest trước khi bỏ profile. Không
xóa manifest/volume khi chưa rõ trạng thái lượt model hoặc fixture.

Suite chat kiểm login PKCE/OTP, câu trả lời Data đối chiếu API, history/reload,
replay/idempotency và anonymous 401. Bật ML/RAG chưa có nghĩa suite này kiểm đủ hai
nhánh: cần kiểm ranking bằng model/context thật và RAG với PR/task có nguồn đã index.
Workflow/email chưa nối đầy đủ nên chưa nghiệm thu được toàn quy trình
PR → approval → PO → email bằng suite hiện tại.

Chi tiết: [SSO](docs/SSO.md), [chat](docs/LANGFLOW-SSO.md), [ML](docs/ML-SERVICE.md),
[RAG](docs/RAG.md), [Agent Hub](docs/AGENT-HUB.md).
