# Quản lý Markdown RAG

Trang **Tài liệu RAG** nằm trong React client hiện có (`clients/sso`), mở bằng
`http://127.0.0.1:8761/#knowledge`. Backend xác minh bearer Keycloak và grant
`ADMIN` còn hiệu lực trong DB cho mọi thao tác, kể cả đọc nội dung/trạng thái.
Không cấp quyền quản lý cho nhân viên kho, người duyệt hay AI service.

## Nguồn và pipeline

Nguồn gốc là `core.documents` và `core.document_versions.content_md` trong PostgreSQL.
Các file ở `examples/rag` là fixture có thể import, không phải một bản nội dung
thứ hai được chỉnh sửa song song. Migration 0004 nhận diện các tài liệu đã tồn tại,
gán tên `<supplier_code>.md` khi metadata đủ an toàn, nếu không dùng `<document_id>.md`.
Không đọc/ghi file tùy ý trên máy chủ. Upload chỉ truyền văn bản UTF-8.

```text
React → API admin → core.knowledge_jobs (pending, bền vững)
                  → worker dùng splitter * / ** / *** và # / ## / ### hiện có
                  → Qwen3-Embedding-0.6B hiện có, 1024 chiều, chuẩn hóa
                  → transaction: phiên bản mới + toàn bộ chunk/vector + trạng thái ready
Langflow → API retrieve-knowledge hiện có → chọn phiên bản theo cutoff của tác vụ
```

Worker dùng chung model/mutex với truy hồi. Tạo embedding trước khi mở transaction
xuất bản. Nếu lỗi, bản nguồn đã xuất bản và vector cũ vẫn hoạt động. Lease, fencing
token và heartbeat trong DB cho phép lấy lại tác vụ sau crash. Không dùng FastAPI
BackgroundTasks hoặc state trên browser làm hàng đợi. Khởi động backend với manager
được bật là đủ để xử lý tác vụ tồn đọng; không cần chạy script ingest thủ công.
Retry lỗi được yêu cầu rõ ràng; không lặp vô hạn khi model/config sai.

Mỗi nhà cung cấp có một tài liệu đang dùng theo unique index Core hiện có. Khi tạo,
quản trị viên chọn nhà cung cấp, người nhận xét, mốc dữ liệu và phạm vi toàn hệ thống
hoặc phòng ban. Người thao tác thật được ghi riêng trong job và phiên bản.
Không suy ra quyền ghi từ tên người nhận xét hay từ phòng ban trên giao diện.

Sửa giữ `document_id`, tăng phiên bản. Chỉ sau khi chunk/vector được kiểm chứng trong
cùng transaction thì bản mới có `published_at/known_from` thực tế. Không backdate.
Không xóa vector của các phiên bản trước: chúng phục vụ truy vết và các tác vụ có
cutoff cũ. Truy hồi hiện hành chỉ chọn phiên bản đúng cutoff, không trộn các bản cũ.
Vì vậy **tác vụ Langflow đã tạo trước lần sửa vẫn có thể thấy bản cũ**. Muốn dùng
tài liệu mới phải dùng tác vụ có cutoff sau thời điểm xuất bản; không sửa cutoff
của tác vụ đã tồn tại để giả cập nhật lịch sử.

Lưu cùng nội dung/tiêu đề không tạo embedding hay tăng phiên bản. Nút **Đồng bộ lại**
luôn dựng lại generation hiện tại, kể cả hash không đổi. Khóa trên document,
`expected_revision`, unique active job và idempotency chống hai lần lưu ghi đè nhau.
DB source/version/generation đang đọc vẫn được kiểm lại tại lúc commit.

## Xóa

**Xóa tài liệu** có xác nhận trên UI. Chỉ ADMIN được yêu cầu. Backend kiểm lại
`risk_analysis_reports.report_data` và `analysis_supplier_candidates.evidence_refs`,
bao gồm tham chiếu document/version/chunk. Nếu đã được trích dẫn, trả 409, không xóa.
Đề xuất tham chiếu báo cáo được bảo vệ thông qua báo cáo đó. Có nút **Ngừng sử dụng**
để loại tài liệu khỏi truy hồi và giữ nguồn lịch sử cho quản trị viên xem.

Với tài liệu tải nhầm chưa được trích dẫn: trong một transaction, đánh dấu xóa,
xóa mọi chunk và phiên bản của đúng tài liệu, xóa document/tên file; scrub nội dung
nháp khỏi các job liên quan. Log job chỉ giữ ID, loại thao tác, người thao tác,
trạng thái, số đoạn, thời điểm. Tạo lại cùng tên nhận ID mới. Trigger khóa tài liệu
khi ghi bằng chứng để không cho báo cáo mới ghi tham chiếu tới nguồn vừa bị xóa.
Không tắt trigger bất biến của phiên bản. Không dùng truncate/drop hoặc tắt RLS.

Phạm vi xóa là kho Core và vector của tài liệu. Chat history của Langflow hay bản
xuất fixture do người dùng giữ là dữ liệu độc lập, không tự bị sửa/xóa theo thao tác này.

## Cài/chạy trên Windows

Từ repository root, dùng môi trường backend sẵn có. Lưu cấu hình DB/IAM trong
`.env` hoặc secret của tiến trình, không đưa lên Git. Backup DB bằng công cụ của
nhóm trước khi nâng migration. Xem SQL dự kiến trước khi chạy:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade 0003_core_knowledge:head --sql
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Migration 0004 bổ sung `core.knowledge_files`, `core.knowledge_jobs` và guard tham
chiếu. Không thay nội dung/version/vector nghiệp vụ hiện có. Tự động downgrade bị
chặn để không làm mất job hoặc nguồn; muốn revert, tắt manager, chờ hết job rồi
review SQL rollback riêng. Không drop bảng Core để rollback.

```powershell
$env:FLOWBRIDGE_RAG_POSTGRES_ENABLED = 'true'
$env:FLOWBRIDGE_RAG_MANAGER_ENABLED = 'true'
# Tối đa 2 MB UTF-8/file, có thể giảm giới hạn:
$env:FLOWBRIDGE_RAG_MANAGER_MAX_BYTES = '2097152'
.\.venv\Scripts\python.exe -m flowbridge.main
```

Model cache Qwen và cấu hình IAM/DB vẫn theo [setup RAG](rag-postgres-setup.md).
Không tự tải model khi có request. Trang quản lý không cần Langflow API key hoặc
flow ingest riêng; ghi đúng kho mà endpoint RAG của Langflow đang đọc.

Frontend theo [hướng dẫn client](../clients/sso/README.md). `npm ci --ignore-scripts`,
`npm run build`, `npm run start:a` trong `clients/sso`; cấu hình public phải trỏ đúng
API và issuer đã đăng ký. Sau đăng nhập ADMIN, chọn **Tài liệu RAG**. Reload trang
rồi chọn lại tài liệu sẽ đọc trạng thái job đã lưu và tiếp tục polling.

API riêng dưới `/api/admin/knowledge`: `GET options`, `GET/POST documents`,
`GET/PUT/DELETE documents/{id}`, `GET documents/{id}/versions/{revision}`,
`POST documents/{id}/reindex`, `POST documents/{id}/withdraw`,
`GET jobs/{id}`, `POST jobs/{id}/retry`. Thao tác ghi cần `Idempotency-Key`;
sửa/xóa/reindex cần `expected_revision`. 202 là đã tiếp nhận, chưa phải đã đồng bộ.
Job ready mới là đã commit. Failed hiển thị mã lỗi an toàn và cho thử lại.

UI chỉ render Markdown bằng React text/heading/paragraph, không thực thi HTML,
ảnh, script hoặc link nhúng. Giữ nguyên các dấu sao trong văn bản nguồn.

## Kiểm tra

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_knowledge_manager.py -q
# DB thật, transaction rollback, embedding kiểm soát; không chứng nhận IAM/model:
.\.venv\Scripts\python.exe -m pytest tests/integration/test_knowledge_manager_database.py -m integration -q
# UI dùng HTTP/Keycloak mock, không ghi dữ liệu Supabase:
npm --prefix clients/sso run test:ui
```

Thử với nhà cung cấp thử nghiệm chưa có tài liệu: tạo câu độc nhất `VERSION_A`,
đợi ready, tạo tác vụ mới và truy vấn; sửa thành `VERSION_B`, đợi ready và dùng tác
vụ mới để kiểm tra bản mới. Lịch sử vẫn có A. Tài liệu khác phải giữ version/chunk.
Xóa tài liệu thử, đợi ready, truy hồi phải không còn A/B từ document đó. Không dùng
test trên tài liệu đã trích dẫn hoặc thay nội dung 6 tài liệu đang dùng chỉ để QA.
