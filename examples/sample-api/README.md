# Gọi API trả dữ liệu mẫu

Chạy từ thư mục gốc FlowBridge sau `uv sync --locked --group build`.

```powershell
$env:FLOWBRIDGE_ENVIRONMENT = "development"
$env:FLOWBRIDGE_SAMPLE_MODE = "true"
$env:FLOWBRIDGE_DOCS_ENABLED = "true"
$env:FLOWBRIDGE_DATABASE_URL = ""
uv run --locked python -m flowbridge.main
```

```sh
FLOWBRIDGE_ENVIRONMENT=development FLOWBRIDGE_SAMPLE_MODE=true FLOWBRIDGE_DOCS_ENABLED=true FLOWBRIDGE_DATABASE_URL= uv run --locked python -m flowbridge.main
```

Mở [Swagger](http://127.0.0.1:8000/docs), chọn operation rồi **Try it out**.
Chế độ sample dùng principal cố định `USER-SAMPLE`/`TENANT-SAMPLE`, không cần token.
Endpoint POST yêu cầu header `Idempotency-Key: sample-001` đúng hình dạng contract;
key chưa được lưu hoặc xử lý replay bền vững. Production từ chối bật sample.

[requests.json](requests.json) có request hợp lệ cho đủ 28 operation: method, path,
query (`params`), body (`json`) và multipart (`upload`/`data`). Dữ liệu tổng hợp do nhóm
tạo, cùng phạm vi GPL-3.0-or-later của source; không chứa thông tin người dùng hoặc nguồn ngoài.

Các lệnh dưới dùng `curl.exe` trong PowerShell; trên Bash thay bằng `curl`.

```powershell
curl.exe http://127.0.0.1:8000/api/v1/core/identity/config
curl.exe http://127.0.0.1:8000/api/v1/core/identity/me
curl.exe http://127.0.0.1:8000/api/v1/core/data/sample_items
curl.exe http://127.0.0.1:8000/api/v1/core/workflow/tasks
curl.exe "http://127.0.0.1:8000/api/v1/core/files/search?q=Synthetic"
curl.exe http://127.0.0.1:8000/api/v1/core/files/FILE-SAMPLE/download
```

PowerShell gửi request ghi không cần escape chuỗi JSON:

```powershell
$body = @{ data = @{ name = "New sample"; status = "draft" } } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/core/data/sample_items -Headers @{ "Idempotency-Key" = "sample-001" } -ContentType application/json -Body $body
```

Để chạy mọi request từ file bằng HTTP thật, dùng công cụ kiểm tra có assertion:

```sh
uv run --locked python scripts/check_sample_api.py --base-url http://127.0.0.1:8000
```

Script chỉ gửi các request trong file mẫu và kiểm tra response; server phải được chạy
bằng sample mode trước. Đây là bằng chứng API mẫu qua TCP, không phải tích hợp engine thật.

| Nhóm | ID/giá trị dùng để gọi thử |
|---|---|
| Data | collection `sample_items`; `REC-SAMPLE` version 2; `REC-SECOND` version 1 |
| Files | `FILE-SAMPLE` clean; `FILE-PENDING` pending (download trả 409) |
| Ingestion | `JOB-SAMPLE`; POST trả queued, GET trả fixture succeeded |
| Workflow | key `sample_approval`; instance `WF-SAMPLE`; task `TASK-SAMPLE` version 1 |
| Notifications | `NOTE-SAMPLE` chưa đọc |

GET trả fixture ổn định. POST/PATCH/action/DELETE trả kết quả minh họa, **không lưu thay đổi**:
GET sau đó vẫn trả fixture ban đầu. ID mới `REC-CREATED`/`FILE-UPLOADED` không xuất hiện
trong GET/list. API transaction chưa commit database; event chưa publish broker; upload
nhận bytes thật rồi trả metadata pending, chưa lưu S3 hoặc quét. Giới hạn upload mặc định 1 MiB,
cấu hình qua `FLOWBRIDGE_SAMPLE_UPLOAD_MAX_BYTES`; vượt giới hạn trả lỗi validation 422.

List có pagination; Data hỗ trợ filter literal `eq`/`in`/`contains` trên `name`/`status`
của fixture, không hỗ trợ `gte`/`lte`. Search tìm literal trong một tài liệu tổng hợp,
chưa có full-text/index/scanner. Không có engine xác thực, storage hoặc BPMN được khởi động.
`/_ops/ready` vẫn 503 vì chưa cấu hình DB, trong khi các API mẫu vẫn gọi được.

Gateway chỉ có [mapping](../../infra/gateway/README.md), gọi trực tiếp backend loopback.
Các endpoint `/internal/*` dùng chung listener development, chưa có network boundary thật.
