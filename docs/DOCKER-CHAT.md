# Chạy chat bằng Docker

Stack này dành cho phát triển local: Keycloak, backend, Langflow và giao diện SSO
chạy cùng Docker Compose; Supabase vẫn là database bên ngoài đang cấu hình trong
`.env`. Đây là overlay của `compose.yaml`, dùng lại generator IAM và công cụ dev
hiện có. Không cài lại Python/Java/Node của máy, không reset user/grant hoặc seed DB.

## Chạy trên máy hiện tại

1. Mở Docker Desktop từ Start menu, chọn Linux containers và chờ Engine chạy.
   Docker CLI/Compose đã có trên máy; chỉ có CLI không chứng minh Engine đã lên.
   Nếu không thấy ứng dụng trong Start, nhấn **Win + R** và mở đúng bản đã cài:

   ```text
   %LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe
   ```

   Nếu chỉ có tiến trình nền nhưng không có cửa sổ, chưa chạy các bước build.
   `wsl -l -v` đang hiển thị `docker-desktop Stopped` và `doctor` thất bại nghĩa
   là Engine chưa sẵn sàng. Không cần xóa distro/volume để chạy cấu hình này.
2. Tại thư mục FlowBridge, kiểm tra cấu hình:

   ```powershell
   cd E:\CTU\CLB_Tin_hoc\OLP\FlowBridge
   .\chat-docker.cmd doctor
   ```

3. Lần đầu, khi Supabase đã kết nối được, áp migration bằng lệnh riêng:

   ```powershell
   .\chat-docker.cmd migrate
   ```

   Lệnh này chạy Alembic chuẩn trên database trong `.env`. Không chạy lại seed.
   `up` chỉ kiểm kết nối/revision và sẽ dừng nếu schema chưa khớp source.

4. Bật stack:

   ```powershell
   .\chat-docker.cmd up
   ```

   Những lần sau dùng lại lệnh này. Để bật cả ML/RAG với artifact đã có:

   ```powershell
   .\chat-docker.cmd up --ml --rag
   ```

   Giữ các cờ `--ml`/`--rag` khi chạy lại nếu vẫn muốn bật chúng. Build đầu tiên có
   thể lâu vì tải image/dependency; không tự tải model weights hoặc gọi OpenAI.

| Thành phần | Địa chỉ mặc định |
|---|---|
| Giao diện đăng nhập/nghiệp vụ | http://127.0.0.1:8761 |
| Swagger/API | http://127.0.0.1:18082/docs |
| Keycloak | https://localhost:19444 |
| Langflow phục vụ chat | http://127.0.0.1:7861 |

Langflow native 7860 và backend test 8001 có thể giữ nguyên; bản Docker dùng state riêng.
Đăng nhập ở8761 bằng tài khoản EMPLOYEE rồi chọn **Trợ lý AI** để chat trực tiếp.
Swagger vẫn có `POST /api/v1/chat/messages` cho tích hợp API. Hội thoại thật có thể
gọi model tính phí theo cấu hình flow. Không tự gọi model khi startup/healthcheck.

## Dữ liệu và secret

Lần đầu, runner dùng SQLite backup API để lấy bản nhất quán từ
`langflow/data/langflow.db`, giữ file `secret_key` gốc. Snapshot ở
`.cache/chat-stack/flowbridge-chat/seed/`, bị Git/build context ignore; sau đó chỉ
import một lần vào volume riêng. Cần đủ 4 flow ver2 và cùng native owner active.
Không đọc từng flow rồi dựng lại bằng preset mặc định hoặc đổi flow đang chỉnh.

Sau startup, runner nâng metadata ver2 cũ trên bản Docker qua API metadata native:
Data tools, coordination và protocol nodes dùng các graph migration đã có trong
repo. Có backup tại `/var/lib/langflow/flowbridge-backups`, kiểm concurrent edit
và rollback khi PATCH lỗi. Giữ model/credential/preset; không thay nguồn native.
Chạy lại trên metadata hiện tại không tạo thay đổi hoặc gọi model.

Secret API key nội bộ và bridge được sinh ngẫu nhiên, lưu trong volume và giữ qua
restart. Dùng cơ chế `LANGFLOW_API_KEY_SOURCE=env` upstream với đúng owner của flow
đã import; auto-login tắt. Database/key Langflow đi cùng nhau để giữ credential mã
hóa. Password editor được giữ nguyên từ DB import, không reset bằng env. Việc đăng
nhập ứng dụng vẫn do Keycloak; không có tài khoản giả hoặc token bỏ qua SSO.

Launcher native ở các nhánh bật `LANGFLOW_AUTO_LOGIN=true`, nên trước Docker không
hiện màn hình login. Tài khoản editor `langflow` sở hữu4flow; nó độc lập với user
SSO của ứng dụng. Ngày19/10, theo lựa chọn rõ của user, đã reset riêng mật khẩu
editor Docker qua API native và kiểm login thành công. Mật khẩu mới ở file ignored
`.cache/chat-stack/flowbridge-chat/langflow-editor-login.json`; không dùng random
startup password trong config làm mật khẩu của user đã tồn tại. Không đổi flow,
tool/model credential, password native nguồn hoặc bật lại auto-login.

Backend đọc riêng DB config, IAM config và chat config. Langflow không mount các
volume DB/IAM; provider key chỉ lấy từ `langflow/data/agent.env` hoặc credential
mã hóa đã có trong DB Langflow. Không sao chép user/AI access token hoặc JobContext
tĩnh trong agent.env sang bản Docker. Backend truyền caller riêng cho từng request.

Muốn xem tài khoản Keycloak local của stack mới, xuất ra file bị ignore:

```powershell
.\chat-docker.cmd credentials
```

Mở riêng `.cache/chat-stack/flowbridge-chat/credentials.json` trên máy. Lệnh dùng
Docker copy file, không đưa mật khẩu qua container stdout/log hoặc terminal.
Không đưa file này vào Git/chat/issue. Certificate CA local được backend dùng
trực tiếp; browser cần tin CA bằng cơ chế trust của môi trường, không tắt TLS.

Docker project mặc định `flowbridge-chat` có IAM state riêng với stack/native cũ.
User Keycloak vẫn phải được liên kết issuer+subject và cấp EMPLOYEE có chủ đích
trong Supabase. Không tự cấp grant chỉ vì đăng nhập thành công. Xem
[SSO.md, bước liên kết user](SSO.md#3-liên-kết-user-và-cấp-quyền-có-chủ-đích).
CLI bootstrap chuẩn có trong image tooling; nếu cần dùng từ Docker, giữ identity
đang chạy và dùng cùng project/config:

```powershell
# Ví dụ xem trước; các UUID phải là tài khoản/phòng ban/admin thực đã kiểm tra.
docker compose --project-name flowbridge-chat --env-file infra/dev/images.env --env-file infra/chat/images.env -f compose.yaml -f infra/chat/compose.yaml run --rm --no-deps tools python scripts/container_chat.py bootstrap --subject UUID_USER_KEYCLOAK --grant EMPLOYEE --department UUID_PHONG_BAN --granted-by UUID_ADMIN_UNG_DUNG
```

Wrapper nạp DB config đã prepare rồi gọi CLI bootstrap chuẩn. Lệnh trên rollback
mặc định; chỉ thêm `--apply` sau khi kiểm tra đúng user/grant.
Trường hợp chưa có ADMIN ứng dụng dùng quy trình bootstrap đầu tiên trong SSO.md;
không reset DB hoặc tự nhận ADMIN từ tài khoản quản trị Keycloak.

## Model và cấu hình

Mặc định chỉ bật API dữ liệu/chat. `--ml`/`--rag` build optional extras đã khai báo
trong pyproject/uv.lock. Cache `.cache/models` mount read-only tại `/models`,
context `.cache/ml` tại `/ml-context`. Có thể chọn thư mục bằng `--models-dir` và
`--ml-dir`. Các ML/RAG settings khác đọc từ `.env`; host paths checkpoint/context
được ánh xạ sang thư mục mount bằng tên file, không ghi Windows path vào image.
Nếu thiếu model/checkpoint/context, startup phải fail; không giả ready.

RAG dùng service client thật `flowbridge-ai`, cần grant AI_SERVICE và task phân
tích thuộc PR của caller. Secret service đọc từ IAM state của chính stack;
không tạo task demo hoặc tự cấp quyền DB. Xem [hợp đồng chat/RAG](LANGFLOW-SSO.md).

Đổi env/provider key rồi chạy lại `up` để prepare lại cấu hình. Compose không tự
restart container khi nội dung file trong volume đổi: nếu process đang chạy và
config thay đổi, dừng bằng `down` rồi `up` để nạp lại; volume giữ nguyên.
Không đổi ports của project đã có state; dùng lại ports cũ hoặc project mới.
Nếu dùng `--project`, `--api-port`, `--iam-port`, `--langflow-port`, truyền nhất
quán ở các lệnh. Snapshot nguồn khác cần project mới; không ghi đè volume đang dùng.

## Quản lý

```powershell
.\chat-docker.cmd status
.\chat-docker.cmd logs
.\chat-docker.cmd down
```

`down` dừng đúng project và giữ volumes. Không có action tự xóa volume hoặc làm
sạch dữ liệu nghiệp vụ. Snapshot và volume Docker không tự đồng bộ các lần sửa
canvas tiếp theo trên Langflow native; bản Docker đã import có state độc lập.

Image Langflow upstream 1.12.6 (MIT) được pin bằng manifest digest, các image khác
dùng manifest chuẩn infra/dev/images.env. Nguồn upstream:
[Docker deployment](https://docs.langflow.org/deployment-docker) và
[API keys](https://docs.langflow.org/api-keys-and-authentication). Source/config
nhóm tạo GPL-3.0-or-later. Không thêm source/binary/cache của dependency vào Git
hoặc source artifact. Image build có dependency cần audit riêng; không coi kiểm
Compose/unit là chứng nhận license/bundling hoặc nghiệm thu toàn image.

## Kết quả kiểm tra ngày 2026-10-18

- 42 test helper/chat/migration đạt; Ruff lint/format và `git diff --check` đạt.
- Compose hợp lệ với tất cả profile ở cấu hình mặc định và `--ml --rag`;
  xác minh merge giữ DB/IAM mounts, secret nguồn và thứ tự startup.
- Snapshot dữ liệu Langflow hiện có đạt integrity check, đủ 4 flow ver2 và khóa gốc.
- Gói source tar.gz có file Docker/adapter cần thiết, không chứa state/cache/secret;
  launcher/config chạy được từ bản giải nén ngoài source tree gốc.
- Docker Engine đã hoạt động; build/start Keycloak/API/Langflow/client đạt.
  Supabase đã áp đúng delta `0004_knowledge_manager` → `0005_chat_projection`;
  SQL knowledge0004 được giữ nguyên byte từ commit `5e96b38` của nhánh
  `origin/minnhungdev_features`. Không chạy lại0004/reset/stamp dữ liệu.
- E2E thật đạt: login PKCE/OTP, chat trả đúng mã/tên 2 sản phẩm qua API, replay
  và history200, anonymous401. Fixture test đã cleanup; stack vẫn đang chạy.
  ML/RAG optional chưa bật/nghiệm thu trong lần này. Xem
  [bằng chứng và lệnh chạy lại](DOCKER-CHAT-LIVE.md).
