# Chạy RAG PostgreSQL và pgvector

Thực hiện trong repo FlowBridge, nhánh minnhungdev_features. Không gửi DSN/mật khẩu/token vào chat. Cấu hình `.env` dựa trên `.env.example`, dùng credential backend PostgreSQL; bộ script không chứa mật khẩu.

## Cài dependency và schema

```powershell
cd C:\Hung\FlowBridge
.\.tools\bootstrap\bin\uv.exe sync --extra rag --group dev
```

Nếu database nghiệp vụ đã ở Alembic 0002, chạy:

```powershell
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts/init_core_knowledge.py --check
```

Migration 0003 tiếp nhận ba bảng Core tương thích đã tạo ngoài Alembic; không DROP/TRUNCATE dữ liệu. Nếu vector đang ở schema khác extensions, cột khác thiết kế hoặc dữ liệu cũ vi phạm bất biến/checksum/thời gian thì transaction dừng, cần đối chiếu hiện trạng. Không tự chuyển extension hoặc xóa bảng để ép chạy.

Nếu nhóm chưa quản lý baseline bằng Alembic và chỉ muốn nâng ba bảng Core hiện có:

```powershell
.\.venv\Scripts\python.exe scripts/init_core_knowledge.py --apply
.\.venv\Scripts\python.exe scripts/init_core_knowledge.py --check
```

Script này chỉ chạy SQL Core và kiểm tra; không tạo 27 bảng nghiệp vụ, không stamp baseline Alembic và không sửa dữ liệu mẫu. Không stamp một database chưa được đối chiếu. Baseline 27 bảng + bảng vận hành api_idempotency của repo cần có trước khi dùng đầy đủ HTTP API.

## Import sáu Markdown và index

Supplier/reviewer UUID trong catalog phải tồn tại trong database tương ứng dữ liệu từ script trước đây. Nếu database được tạo bằng seed khác thì sửa mapping được xác minh, không bỏ qua kiểm tra binding.

```powershell
$env:OMP_NUM_THREADS = "4"
$env:HF_HUB_OFFLINE = "1"
.\.venv\Scripts\python.exe -m flowbridge.supplier_assistant.knowledge.postgres_cli import
.\.venv\Scripts\python.exe -m flowbridge.supplier_assistant.knowledge.postgres_cli index
```

Nếu máy chưa có model cache, bỏ HF_HUB_OFFLINE và chạy `index --allow-download`. Đây chỉ tải model embedding công khai đã pin; không upload Markdown. Có thể thêm `--department <uuid>` trước subcommand để import hồ sơ giới hạn phòng ban. Default import là hồ sơ global; operator quyết định scope với credential backend.

Import là nguồn dữ liệu fixture, giữ attribution giả lập. `known_from` bắt đầu ở thời điểm publication/import thực tế. Vì vậy job A/B có cutoff trước lần import đầu sẽ chưa thấy các Markdown này. Tạo job thử mới sau import; không backdate known_from để làm demo lịch sử trông đúng. Mỗi hồ sơ import trong transaction riêng và retry không sinh version thừa nếu nội dung/metadata hiện hành giống nhau.

Quét index lại sau mỗi publication/correction. Chưa có daemon tự nhận outbox. Các version cũ còn published cũng được index để replay A/B; tăng version không ghi đè version cũ.

## Thử truy hồi bằng CLI

```powershell
.\.venv\Scripts\python.exe -m flowbridge.supplier_assistant.knowledge.postgres_cli --supplier 3806e329-2ebe-5cfc-895d-1bd60894b348 search "Đơn đặt hàng DEMO-PO-00212 giao trễ như thế nào?" --top-k 3
```

CLI là operator đã có credential database, không phải chứng minh phân quyền HTTP. Replay có thể thêm `--as-of "2026-10-16T12:00:00+07:00"` trước subcommand nếu mốc đó sau publication và không ở tương lai. `references` xuất refs chuẩn bị cho HTTP index; `inspect <reference.json>` nhận một JSON DocumentReference gồm sáu field để resolve quote cũ còn được phép đọc.

## Bật HTTP

```powershell
$env:FLOWBRIDGE_RAG_DEV_ENABLED = "false"
$env:FLOWBRIDGE_RAG_POSTGRES_ENABLED = "true"
$env:FLOWBRIDGE_DOCS_ENABLED = "true"
$env:FLOWBRIDGE_DATABASE_COMMAND_TIMEOUT_SECONDS = "300"
.\.venv\Scripts\python.exe -m flowbridge.main
```

Giữ credential database/IdP trong `.env` hoặc secret manager. Cần cấu hình issuer, audience, introspection URL, client ID/secret của IdP hiện có và ánh xạ identity AI service tới user/grant AI_SERVICE active. Nếu IdP chưa nối, HTTP không bỏ qua xác thực mà trả lỗi dependency/unauthenticated; vẫn có thể thử operator CLI.

Sau khi đồng bộ main có Keycloak, registry phải nhận diện đúng client AI là service. Với tên client mặc định trong realm của repo, cấu hình trong `.env`:

```dotenv
FLOWBRIDGE_OIDC_ALLOWED_CLIENTS=["flowbridge-web-a","flowbridge-web-b","flowbridge-ai","flowbridge-po"]
FLOWBRIDGE_OIDC_SERVICE_CLIENTS=["flowbridge-ai","flowbridge-po"]
```

Nếu nhóm đổi client ID thì dùng registry tương ứng. Token gọi RAG phải thuộc client AI được đăng ký; user liên kết trong PostgreSQL phải có `account_type=service` và grant `AI_SERVICE` đang hoạt động. Registry không thay thế grant trong database. Xem [hướng dẫn SSO](SSO.md) để cấu hình issuer, audience, introspection và bootstrap identity; không cần tạo lại bảng RAG.

`POST /internal/v1/tools/retrieve-knowledge`:

```http
Authorization: Bearer <access token của AI service>
Idempotency-Key: rag-query-001
X-Job-Context: <UUID analysis_tasks thật>
Content-Type: application/json
```

```json
{"query":"Đơn đặt hàng DEMO-PO-00212 giao trễ như thế nào?","supplier_id":"3806e329-2ebe-5cfc-895d-1bd60894b348","top_k":3}
```

JobContext chỉ nhận task UUID, không nhận JSON user/role/cutoff. Receiver lấy feature_asof và phòng ban từ database, kiểm tra PR/revision/trạng thái. Không gửi X-User-Context kèm theo. `POST /internal/v1/tools/index-knowledge` dùng cùng headers, body `{"documents":[<DocumentReference chuẩn bị>]}` và trả 200 sau khi xử lý. Để index nhiều nguồn ban đầu, nên dùng CLI tránh giới hạn HTTP timeout.

## Kiểm tra

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend scripts tests
```

Bốn test database mới chỉ chạy khi chủ động cấu hình database test có migration 0003:

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts="" -m integration tests/integration/test_knowledge_pgvector_database.py -q
```

Chúng tạo hồ sơ fixture trong outer transaction rồi rollback, dùng vector điều khiển để kiểm tra adapter; không thay benchmark Qwen. Không dùng database production làm môi trường kiểm thử thường xuyên.

Kiểm chứng SQL cục bộ tùy chọn, không phải dependency runtime backend:

```powershell
.\.venv\Scripts\python.exe scripts/export_rag_sql_fixture.py
npm install --ignore-scripts --no-save --prefix .cache/rag/pg-sql-tools @electric-sql/pglite@0.5.10 @electric-sql/pglite-pgvector@0.0.11
$env:NODE_PATH = "$PWD/.cache/rag/pg-sql-tools/node_modules"
node tests/sql/knowledge_pgvector.mjs
```

Kết quả SQL trong `.cache/rag/pgvector-sql-results.json`; không commit npm packages/cache hoặc credential. PGlite direct SQL kiểm tra migration/trigger/query, chưa thay kết quả asyncpg/Supabase E2E.
