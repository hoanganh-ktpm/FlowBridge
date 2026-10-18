# Hướng dẫn chạy FlowBridge

Cập nhật ngày **19/10/2026**. Hướng dẫn này áp dụng cho bản checkout hiện tại trên
nhánh `huuhuandev_features`, gồm backend, Keycloak, Langflow và giao diện có khung
chat. Đây là môi trường phát triển local; Supabase là database bên ngoài Docker.

## 1. Chạy nhanh trên máy hiện tại

Máy hiện tại đã có cấu hình database, flow Langflow, tài khoản test và migration.
Không cần tạo lại môi trường hoặc chạy lại fixture tài khoản.

1. Mở **Docker Desktop**, chờ Linux Engine hoạt động.
2. Mở PowerShell và chạy:

   ```powershell
   cd E:\CTU\CLB_Tin_hoc\OLP\FlowBridge
   .\chat-docker.cmd doctor
   .\chat-docker.cmd up
   .\chat-docker.cmd status
   ```

3. Mở [giao diện FlowBridge](http://127.0.0.1:8761/), đăng nhập bằng
   `flowbridge-other`, rồi chọn tab **Trợ lý AI**.

Lần build đầu có thể lâu vì tải image và dependency. Những lần sau Docker dùng
lại cache và volume. Giữ nguyên Docker project mặc định `flowbridge-chat` để dùng
lại tài khoản, flow và cấu hình đã chuẩn bị.

Nếu không thấy Docker Desktop trong Start menu, bản đã cài trên máy này nằm tại:

```text
%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe
```

Nhấn **Win + R**, dán đường dẫn trên rồi Enter. Kết quả `doctor` thành công phải
có dòng `PASS: chat Compose configuration and Linux engine`.

## 2. Các địa chỉ cần biết

| Thành phần | Địa chỉ | Dùng để làm gì? |
|---|---|---|
| Ứng dụng FlowBridge | http://127.0.0.1:8761/ | Đăng nhập SSO, nghiệp vụ và chat |
| Backend Swagger | http://127.0.0.1:18082/docs | Xem và thử API |
| Langflow editor Docker | http://127.0.0.1:7861/ | Xem/chỉnh sửa 4 flow ver2 |
| Keycloak Admin | https://localhost:19444/admin/ | Quản lý tài khoản đăng nhập SSO |

Langflow native cũ dùng port `7860`; backend test cũ dùng `8001`. Chúng là môi
trường riêng, không phải đường chat SSO của stack Docker này.

## 3. Tài khoản và mật khẩu

### Đăng nhập ứng dụng tại 8761

Các tài khoản dưới đây đã được liên kết và cấp quyền trên môi trường hiện tại:

| Tài khoản | Quyền ứng dụng | Cách sử dụng |
|---|---|---|
| `flowbridge-other` | EMPLOYEE | Nên dùng để thử nghiệp vụ và chat |
| `flowbridge-admin` | ADMIN | Quản trị ứng dụng; không tự có quyền EMPLOYEE |
| `flowbridge-employee` | EMPLOYEE | Tài khoản dành cho kiểm thử tự động, đã có OTP |

Mật khẩu nằm trong file riêng, mục `keycloak_users`:

```text
.cache/chat-stack/flowbridge-chat/credentials.json
```

Mở file bằng trình soạn thảo trên máy, tìm đúng username và trường `password`.
Nếu cần xuất lại tài khoản Keycloak của chính stack đang chạy:

```powershell
.\chat-docker.cmd credentials
```

Với tài khoản chưa thiết lập OTP, Keycloak sẽ hiển thị mã QR ở lần đầu. Dùng ứng
dụng Authenticator quét QR và nhập mã 6 số để hoàn tất. Những lần sau dùng mã từ
ứng dụng đó. Không đăng ký một tài khoản mới chỉ vì chưa có mã OTP của tài khoản cũ.

### Đăng nhập Langflow editor tại 7861

- Username: `langflow`.
- Mật khẩu Docker đã được đặt lại theo yêu cầu và kiểm tra đăng nhập thành công.
  Xem file riêng:

  ```text
  .cache/chat-stack/flowbridge-chat/langflow-editor-login.json
  ```

Tài khoản `langflow` sở hữu 4 flow: Trung tâm, Dữ liệu, Dự đoán ML và Tri thức RAG.
Tài khoản editor mới như `huan` có workspace riêng nên có thể trống; đăng ký thành
công không tự cấp quyền xem flow của tài khoản khác.

Trước khi chuyển sang Docker, launcher native bật `LANGFLOW_AUTO_LOGIN=true`, nên
không hỏi mật khẩu. Docker tắt auto-login. Tài khoản SSO `flowbridge-other` không
dùng để đăng nhập editor; người dùng khung chat cũng không cần đăng nhập editor.

### Đăng nhập Keycloak Admin

Sử dụng thông tin trong mục `keycloak_admin` của `credentials.json`.
Đây là tài khoản quản trị hệ thống đăng nhập, khác với `flowbridge-admin` của
ứng dụng. Không đưa mật khẩu, file credential hoặc ảnh QR vào Git/chat/issue.

## 4. Sử dụng khung chat

1. Đăng nhập ứng dụng tại `http://127.0.0.1:8761/` bằng tài khoản có EMPLOYEE.
2. Chọn **Trợ lý AI**.
3. Có thể chọn **Tải yêu cầu của tôi** rồi chọn PR nếu câu hỏi gắn với yêu cầu mua
   hàng. Để **Tra cứu chung** khi chỉ hỏi danh mục.
4. Nhập câu hỏi và bấm **Gửi tin nhắn**. Ví dụ:

   ```text
   Liệt kê 2 sản phẩm đang hoạt động, gồm mã và tên.
   ```

5. Chờ câu trả lời; trong lúc xử lý không gửi lặp lại. Một hội thoại mới không
   hủy lượt đang chạy của hội thoại cũ.

Khung chat dùng phiên SSO để gọi backend; backend kiểm quyền rồi gọi Langflow.
Không cần nhập API key hoặc mật khẩu Langflow vào khung chat.

**Tải lại hội thoại** đọc lịch sử đã lưu. Reload trang vẫn có thể khôi phục phiên
SSO và hội thoại từ URL. Mã hội thoại trong URL không cấp quyền cho người khác.
Backend hiện không đưa lịch sử các lượt trước vào model, nên mỗi câu hỏi cần nêu
đủ sản phẩm, nhà cung cấp và điều kiện cần tra cứu.

Nếu báo chưa xác định kết quả, dùng **Kiểm tra lại lượt gửi**. Nút này giữ nguyên
nội dung và Idempotency-Key; không tự tạo một lượt model mới. Hội thoại thật có
thể phát sinh phí của nhà cung cấp model theo cấu hình hiện có.

## 5. Dừng, chạy lại và cập nhật giao diện

```powershell
# Xem trạng thái
.\chat-docker.cmd status

# Xem log gần nhất — kiểm tra và che thông tin nhạy cảm trước khi chia sẻ
.\chat-docker.cmd logs

# Dừng project, giữ volume
.\chat-docker.cmd down

# Chạy lại, build source nếu cần
.\chat-docker.cmd up
```

Nếu thay `.env` hoặc provider key trong `langflow/data/agent.env`, dùng `down`
rồi `up` để process nạp cấu hình mới. Chạy `up` đơn thuần không bảo đảm process
đang chạy sẽ nạp lại nội dung file cấu hình trong volume.

Không xóa Docker volumes hoặc chạy fixture `cleanup` khi vẫn muốn giữ các tài
khoản test và dữ liệu đã tạo. Snapshot/volume Docker không tự đồng bộ các lần sửa
flow tiếp theo trên Langflow native; hai bản có state độc lập.

## 6. Chuẩn bị khi chuyển sang máy mới

Phần chạy nhanh ở trên không phải quy trình cài từ một bản clone trắng. Bản hiện
tại có thay đổi chưa commit; cần bàn giao đúng source trước khi dùng trên máy khác.

Các đầu vào cần chuẩn bị:

- Docker Desktop/Linux Engine và Docker Compose **>= 2.35.0**.
- Python **3.12** theo toolchain của dự án để chạy launcher. File `.cmd` ưu tiên Python trong
  `.venv`, nếu không có sẽ gọi `python` trên PATH. Java/Node và dependency dịch vụ
  được build bên trong container.
- `.env` trỏ đúng Supabase; `.env.example` chỉ là mẫu và không chứa credential.
- Langflow đã có đủ 4 flow ver2 trong `langflow/data/langflow.db` cùng file
  `langflow/data/secret_key`. Chúng phải đi cùng nhau để đọc credential mã hóa.
  Có thể chuẩn bị/import bằng hướng dẫn [Langflow](langflow/README.md) và
  [ver2](langflow/VER2.md). Không chỉ lấy source rồi tự nhận đã có DB/flow local.
- File `langflow/data/agent.env` và cấu hình provider/model hợp lệ. Xem mẫu
  `langflow/agent.env.example`; Docker chỉ lấy provider settings cần thiết,
  không dùng user bearer tĩnh để thay SSO.

Chỉ tạo `.env` từ mẫu nếu file chưa tồn tại:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

Điền kết nối DB bằng URL hoặc các trường thành phần, ví dụ:

```dotenv
FLOWBRIDGE_DATABASE_HOST=HOST_POOLER_TU_SUPABASE
FLOWBRIDGE_DATABASE_USER=postgres.PROJECT_REF
FLOWBRIDGE_DATABASE_PORT=5432
FLOWBRIDGE_DATABASE_NAME=postgres
FLOWBRIDGE_DATABASE_PASSWORD='MAT_KHAU_CUA_BAN'
FLOWBRIDGE_DATABASE_SSL=require
```

Nếu `FLOWBRIDGE_DATABASE_URL` có giá trị thì nó được ưu tiên hơn các trường thành
phần. Lấy host/user/port chính xác từ cấu hình kết nối Supabase của môi trường đó.

Kiểm cấu hình, áp migration khi đã xác định đúng database, rồi khởi động:

```powershell
.\chat-docker.cmd doctor
.\chat-docker.cmd migrate
.\chat-docker.cmd up
```

`migrate` thực sự thay đổi schema DB được chọn. Máy hiện tại đã ở revision
`0005_chat_projection`; không cần chạy migration để mỗi lần mở ứng dụng. `up`
chỉ kiểm DB head và không tự migrate/seed. Nếu gặp revision không có trong source,
đối chiếu đúng migration; không dùng stamp/reset/downgrade để vượt kiểm tra.

Project mới sinh IAM state riêng. Đăng nhập Keycloak thành công chưa có nghĩa là
đã có quyền ứng dụng: cần liên kết issuer+subject và cấp EMPLOYEE theo
[hướng dẫn SSO](docs/SSO.md#3-liên-kết-user-và-cấp-quyền-có-chủ-đích).
Các file credential/test account trên máy hiện tại không tự có ở máy mới.

## 7. Bật ML/RAG khi có đủ dữ liệu và artifact

Mặc định `up` bật chat và API dữ liệu. Các nhánh ML/RAG cần dependency và đầu vào
riêng; không coi có node trong canvas là engine đã sẵn sàng.

```powershell
.\chat-docker.cmd up --ml --rag
```

Giữ các cờ đó ở những lần chạy sau nếu vẫn muốn bật. Cần checkpoint/context ML,
cache model, cấu hình pgvector và service grant phù hợp. RAG trong chat còn cần
analysis job thật thuộc PR được phép truy cập; không tạo JobContext giả.
Runner không tự tải model weights. Xem [ML](docs/ML-SERVICE.md),
[RAG](docs/rag-postgres-setup.md) và [Docker chat](docs/DOCKER-CHAT.md).

## 8. Xử lý lỗi thường gặp

| Hiện tượng | Kiểm tra / cách xử lý |
|---|---|
| `doctor` báo chưa có Linux Engine | Mở Docker Desktop, chờ Engine; kiểm `wsl -l -v` nếu Docker đang chạy nền |
| Không mở được trang 8761 | Chạy `status`, xem `logs`; kiểm port8761 có bị ứng dụng khác chiếm không |
| Browser báo lỗi chứng chỉ Keycloak | Cấu hình tin CA local của đúng profile theo [SSO](docs/SSO.md); không tắt TLS verification |
| `up` dừng ở DB revision/connectivity | Kiểm kết nối Supabase và migration đúng source; không tự seed/reset |
| Tab Trợ lý AI bị khóa | Đăng nhập bằng user có EMPLOYEE active, ví dụ `flowbridge-other`; ADMIN không tự có EMPLOYEE |
| HTTP401/403 dù login được | Kiểm phiên SSO, issuer+subject và grant ứng dụng; không thay bằng token giả |
| Langflow báo Waiting for approval | Tài khoản đăng ký mới cần quản trị viên Langflow kích hoạt |
| Editor Langflow trống với `huan` | Flow thuộc user `langflow`; dùng tài khoản owner hoặc cơ chế cấp quyền phù hợp |
| Chat503/504 | Kiểm Langflow healthy, provider key/model, kết nối mạng và log; không gửi liên tiếp để né lỗi |
| Cảnh báo tra cứu tài liệu chưa khả dụng | Kiểm cấu hình RAG/service grant và analysis job hợp lệ của PR; chat dữ liệu có thể vẫn dùng được |
| UI chưa thấy tab mới sau sửa source | Chạy lại `up` để build/recreate client và tải lại trình duyệt |
| Thiếu snapshot/encryption key khi chuyển máy | Bàn giao DB Langflow và khóa tương ứng hoặc chuẩn bị/import flow theo hướng dẫn; không xóa volume đang có |

## 9. Kiểm thử và tài liệu liên quan

Kiểm cấu hình không cần chạy model:

```powershell
.\chat-docker.cmd config
.\chat-docker.cmd up --dry-run
```

Unit backend/helper, khi môi trường phát triển Python đã được chuẩn bị:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_chat_stack.py tests/unit/test_chat_backend.py tests/unit/test_chat_migration.py -q
```

Kiểm thử UI bằng mock và hướng dẫn chạy E2E có chủ đích nằm trong
[README client](clients/sso/README.md) và [bằng chứng chat thật](docs/DOCKER-CHAT-LIVE.md).
E2E thật gọi model; không chạy lại bước tạo/cleanup fixture trên các tài khoản
test đang được giữ để dùng thủ công.

Đã có bằng chứng 13 test UI và 1 E2E qua khung chat thật đạt trên môi trường này;
ML/RAG optional chưa được nghiệm thu trong bài kiểm đó. Đây là kết quả được ghi
nhận, không thay việc kiểm môi trường mới. Chi tiết vận hành:
[Docker](docs/DOCKER-CHAT.md), [SSO](docs/SSO.md),
[backend ↔ Langflow](docs/LANGFLOW-SSO.md), [API database](docs/SUPABASE-API.md).
